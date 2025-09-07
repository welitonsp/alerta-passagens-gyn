#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Monitoramento de Passagens Aéreas (Amadeus + Telegram) — SANDBOX por padrão.

- Busca ofertas em datas aleatórias nos próximos N dias.
- Encontra a mais barata por destino e envia para o Telegram.
- Salva histórico em CSV.
- Extrai nome da companhia quando disponível (via dictionaries.carriers).

Produção só é ativada se:
  AMADEUS_ENV=production  e  ALLOW_PROD=1
"""

import os
import sys
import csv
import time
import random
from pathlib import Path
from datetime import datetime, timedelta
import requests

# -----------------------
# Ambiente (TRAVADO)
# -----------------------
ENV_RAW = (os.getenv("AMADEUS_ENV") or "sandbox").strip().lower()
ALLOW_PROD = (os.getenv("ALLOW_PROD", "0").strip().lower() in ("1", "true", "yes"))
ENV = "production" if (ENV_RAW in ("production", "prod") and ALLOW_PROD) else "sandbox"
BASE_URL = "https://api.amadeus.com" if ENV == "production" else "https://test.api.amadeus.com"

# -----------------------
# Credenciais & Telegram
# -----------------------
CLIENT_ID = os.getenv("AMADEUS_API_KEY")
CLIENT_SECRET = os.getenv("AMADEUS_API_SECRET")

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# -----------------------
# Config
# -----------------------
def brazil_capitals_iata():
    # Capitais + hubs mais comuns (GRU/CGH incluídos)
    return [
        "GIG","SDU","SSA","FOR","REC","NAT","MCZ","AJU",
        "MAO","BEL","SLZ","THE","BSB","FLN","POA","CWB",
        "CGR","CGB","CNF","VIX","JPA","PMW","PVH","BVB",
        "RBR","GYN","GRU","CGH"
    ]

def dedupe_keep_order(seq):
    seen = set()
    out = []
    for x in seq:
        if x and x not in seen:
            seen.add(x)
            out.append(x)
    return out

class Config:
    ORIGEM = os.getenv("ORIGEM", "GYN").strip().upper()
    # Se DESTINOS não vier, usa capitais
    _dests = os.getenv("DESTINOS", ",".join(brazil_capitals_iata()))
    DESTINOS = [x.strip().upper() for x in _dests.split(",") if x.strip()]
    DESTINOS = [d for d in DESTINOS if d != ORIGEM]
    DESTINOS = dedupe_keep_order(DESTINOS)

    DAYS_AHEAD_FROM = int(os.getenv("DAYS_AHEAD_FROM", "10"))
    DAYS_AHEAD_TO   = int(os.getenv("DAYS_AHEAD_TO", "90"))
    SAMPLE_DEPARTURES = int(os.getenv("SAMPLE_DEPARTURES", "2"))
    MAX_OFFERS = int(os.getenv("MAX_OFFERS", "5"))
    CURRENCY = os.getenv("CURRENCY", "BRL")
    REQUEST_DELAY = float(os.getenv("REQUEST_DELAY", "1.2"))

# Alertas
MAX_PRECO_PP = float(os.getenv("MAX_PRECO_PP", "1200"))
MIN_DISCOUNT_PCT = float(os.getenv("MIN_DISCOUNT_PCT", "0.25"))

# Histórico
HISTORY_PATH = Path(os.getenv("HISTORY_PATH", "data/history.csv"))
HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
CSV_HEADERS = ["ts_utc","origem","destino","departure_date","price_total","currency","notified","reason","airline"]

# -----------------------
# Utils
# -----------------------
def log(msg, level="INFO"):
    icons = {"INFO":"ⓘ","SUCCESS":"✅","ERROR":"❌","WARNING":"⚠️"}
    print(f"[{datetime.utcnow().isoformat()}Z] {icons.get(level,' ')} {msg}")

def enviar_telegram(text: str):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        log("Telegram não configurado (TOKEN/CHAT_ID ausentes).", "WARNING")
        return
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": text},
            timeout=30,
        )
        r.raise_for_status()
        log("Mensagem enviada ao Telegram.", "SUCCESS")
    except requests.RequestException as e:
        log(f"Erro ao enviar Telegram: {e}", "ERROR")

def append_history_row(row: dict):
    write_header = not HISTORY_PATH.exists()
    try:
        with HISTORY_PATH.open("a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=CSV_HEADERS)
            if write_header:
                w.writeheader()
            w.writerow(row)
    except OSError as e:
        log(f"Erro ao gravar histórico: {e}", "ERROR")

# -----------------------
# Amadeus helpers
# -----------------------
def get_token() -> str:
    if not CLIENT_ID or not CLIENT_SECRET:
        log("AMADEUS_API_KEY/AMADEUS_API_SECRET ausentes.", "ERROR")
        sys.exit(1)
    try:
        resp = requests.post(
            f"{BASE_URL}/v1/security/oauth2/token",
            data={
                "grant_type": "client_credentials",
                "client_id": CLIENT_ID,
                "client_secret": CLIENT_SECRET,
            },
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()["access_token"]
    except requests.RequestException as e:
        log(f"Falha ao obter token: {e}", "ERROR")
        sys.exit(1)

def buscar_passagens(token, origem, destino, date_str):
    params = {
        "originLocationCode": origem,
        "destinationLocationCode": destino,
        "departureDate": date_str,
        "adults": "1",
        "currencyCode": Config.CURRENCY,
        "max": str(Config.MAX_OFFERS),
    }
    try:
        r = requests.get(
            f"{BASE_URL}/v2/shopping/flight-offers",
            headers={"Authorization": f"Bearer {token}"},
            params=params,
            timeout=60,
        )
        r.raise_for_status()
        return r.json()
    except requests.RequestException as e:
        log(f"Erro {origem}->{destino} {date_str}: {e}", "ERROR")
        return None

def extract_airline(offer: dict, dictionaries: dict) -> str:
    """
    Tenta obter o nome da cia:
    1) operatingCarrier / marketingCarrier (IATA) -> dicionário 'carriers'
    2) cai para o código IATA se nome não existir
    """
    try:
        seg0 = offer["itineraries"][0]["segments"][0]
        op = seg0.get("operating", {}).get("carrierCode") or seg0.get("operatingCarrierCode") \
             or seg0.get("operatingCarrier")
        mk = seg0.get("carrierCode") or seg0.get("marketingCarrierCode") \
             or seg0.get("marketingCarrier")
        code = op or mk
        if not code:
            return "N/A"
        name = None
        if dictionaries and "carriers" in dictionaries:
            name = dictionaries["carriers"].get(code)
        return name or code
    except Exception:
        return "N/A"

def find_cheapest_offer(payload: dict):
    if not payload or "data" not in payload or not payload["data"]:
        return None, None
    try:
        cheapest = min(
            payload["data"],
            key=lambda x: float(x.get("price", {}).get("total", float("inf")))
        )
        dictionaries = payload.get("dictionaries", {})
        return cheapest, dictionaries
    except Exception as e:
        log(f"Erro ao escolher mais barata: {e}", "ERROR")
        return None, None

# -----------------------
# Lógica & datas
# -----------------------
def gerar_datas():
    today = datetime.utcnow().date()
    out = []
    for _ in range(Config.SAMPLE_DEPARTURES):
        delta = random.randint(Config.DAYS_AHEAD_FROM, Config.DAYS_AHEAD_TO)
        out.append((today + timedelta(days=delta)).strftime("%Y-%m-%d"))
    return out

def deve_alertar(preco_atual: float, melhor_anterior: float | None):
    # 1) teto absoluto
    if preco_atual <= MAX_PRECO_PP:
        return True, f"≤ teto {int(MAX_PRECO_PP) if MAX_PRECO_PP.is_integer() else MAX_PRECO_PP}"
    # 2) queda percentual relevante
    if melhor_anterior is not None and melhor_anterior not in (0, float("inf")):
        queda = (melhor_anterior - preco_atual) / melhor_anterior
        if queda >= MIN_DISCOUNT_PCT:
            return True, f"queda {queda:.0%}"
    return False, "sem queda suficiente"

def resumo_msg(origem, destino, date_str, price, currency, airline, motivo):
    price_fmt = f"{price:.2f}"
    return f"✈️ {origem} → {destino} em {date_str}: {price_fmt} {currency} ({airline}) — {motivo}"

def process_destino(token, origem, destino, melhores: dict):
    log(f"🔎 {origem} → {destino}")
    best_key = (origem, destino)
    melhor_anterior = melhores.get(best_key, float("inf"))

    for date_str in gerar_datas():
        time.sleep(Config.REQUEST_DELAY)
        payload = buscar_passagens(token, origem, destino, date_str)
        if not payload:
            continue

        cheapest, dictionaries = find_cheapest_offer(payload)
        if not cheapest:
            continue

        price = float(cheapest["price"]["total"])
        currency = cheapest["price"].get("currency", Config.CURRENCY)
        airline = extract_airline(cheapest, dictionaries)

        # Tentativa segura da data real do voo
        try:
            date_real = cheapest["itineraries"][0]["segments"][0]["departure"]["at"][:10]
        except Exception:
            date_real = date_str

        alert, motivo = deve_alertar(price, melhor_anterior)
        notified = False
        if alert:
            enviar_telegram(resumo_msg(origem, destino, date_real, price, currency, airline, motivo))
            notified = True

        append_history_row({
            "ts_utc": datetime.utcnow().isoformat()+"Z",
            "origem": origem,
            "destino": destino,
            "departure_date": date_real,
            "price_total": f"{price:.2f}",
            "currency": currency,
            "notified": "1" if notified else "0",
            "reason": motivo,
            "airline": airline,
        })

        if price < melhor_anterior:
            melhor_anterior = price
            melhores[best_key] = price

def main():
    banner_env = "🚀 PRODUÇÃO" if ENV == "production" else "🔧 SANDBOX"
    log(f"Iniciando monitor | ENV={ENV} ({banner_env}) | BASE={BASE_URL}")
    token = get_token()
    melhores = {}
    for dest in Config.DESTINOS:
        process_destino(token, Config.ORIGEM, dest, melhores)
    log("Monitoramento concluído.", "SUCCESS")

if __name__ == "__main__":
    main()