#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Monitoramento de Passagens Aéreas (Amadeus + Telegram) - Produção

- Lê variáveis de ambiente
- Obtém token OAuth2 da Amadeus
- Busca ofertas (v2/shopping/flight-offers)
- Aplica regras de alerta (queda % > teto)
- Envia para Telegram (com companhia e link)
- Persiste histórico em data/history.csv
"""

from __future__ import annotations

import csv
import os
import random
import sys
import time
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, Tuple

import requests

# =========================
# Config
# =========================
class Config:
    ORIGEM = os.getenv("ORIGEM", "GYN").strip().upper()

    DESTINOS = list(dict.fromkeys(
        (os.getenv(
            "DESTINOS",
            "GIG,SDU,SSA,FOR,REC,NAT,MCZ,AJU,MAO,BEL,SLZ,THE,BSB,FLN,POA,CWB,CGR,CGB,CNF,VIX,JPA,PMW,PVH,BVB,RBR,GYN,GRU,CGH"
        )).replace(" ", "").split(",")
    ))
    DESTINOS = [d for d in DESTINOS if d]
    DESTINOS = list(dict.fromkeys(DESTINOS))

    DAYS_AHEAD_FROM = int(os.getenv("DAYS_AHEAD_FROM", "10"))
    DAYS_AHEAD_TO   = int(os.getenv("DAYS_AHEAD_TO", "90"))
    SAMPLE_DEPARTURES = int(os.getenv("SAMPLE_DEPARTURES", "2"))
    CURRENCY = os.getenv("CURRENCY", "BRL")

    MAX_OFFERS = int(os.getenv("MAX_OFFERS", "5"))
    REQUEST_DELAY = float(os.getenv("REQUEST_DELAY", "1.2"))  # anti rate limit

# =========================
# Ambiente / endpoints
# =========================
CLIENT_ID = os.getenv("AMADEUS_API_KEY")
CLIENT_SECRET = os.getenv("AMADEUS_API_SECRET")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

ENV = os.getenv("AMADEUS_ENV", "test").strip().lower()
BASE_URL = "https://test.api.amadeus.com" if ENV == "test" else "https://api.amadeus.com"

# Regras
MAX_PRECO_PP = float(os.getenv("MAX_PRECO_PP", "1200"))
MIN_DISCOUNT_PCT = float(os.getenv("MIN_DISCOUNT_PCT", "0.25"))

# Histórico CSV
HISTORY_PATH = Path(os.getenv("HISTORY_PATH", "data/history.csv"))
HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
CSV_HEADERS = [
    "ts_utc", "origem", "destino", "departure_date",
    "price_total", "currency", "airline", "deeplink",
    "notified", "reason"
]

# =========================
# Utils
# =========================
def log(msg: str, level: str = "INFO"):
    icons = {"INFO": "ⓘ", "SUCCESS": "✅", "ERROR": "❌", "WARNING": "⚠️"}
    print(f"[{datetime.utcnow().isoformat()}Z] {icons.get(level,' ')} {msg}")

def enviar_telegram(texto: str):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        log("Telegram não configurado. Pulando envio.", "WARNING")
        return
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": texto,
                "disable_web_page_preview": True
            },
            timeout=30,
        )
        r.raise_for_status()
        log("Mensagem enviada ao Telegram.", "SUCCESS")
    except requests.RequestException as e:
        log(f"Erro ao enviar para Telegram: {e}", "ERROR")

def append_history_row(row: Dict[str, str]):
    write_header = not HISTORY_PATH.exists()
    try:
        with HISTORY_PATH.open("a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=CSV_HEADERS)
            if write_header:
                w.writeheader()
            w.writerow(row)
    except OSError as e:
        log(f"Erro ao escrever histórico: {e}", "ERROR")

def load_best_prices() -> Dict[Tuple[str, str], float]:
    best: Dict[Tuple[str, str], float] = {}
    if not HISTORY_PATH.exists():
        return best
    try:
        with HISTORY_PATH.open("r", encoding="utf-8") as f:
            r = csv.DictReader(f)
            for row in r:
                try:
                    key = (row["origem"], row["destino"])
                    price = float(row["price_total"])
                    if key not in best or price < best[key]:
                        best[key] = price
                except Exception:
                    continue
    except OSError as e:
        log(f"Erro ao ler histórico: {e}", "WARNING")
    return best

# =========================
# Amadeus API
# =========================
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

def buscar_passagens(token: str, origem: str, destino: str, data: str):
    params = {
        "originLocationCode": origem,
        "destinationLocationCode": destino,
        "departureDate": data,
        "adults": "1",
        "currencyCode": Config.CURRENCY,
        "max": Config.MAX_OFFERS,
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
        log(f"Erro na busca {origem}->{destino} {data}: {e}", "ERROR")
        return None

def find_cheapest_offer(offers):
    """Retorna a oferta mais barata + 'airline' (operating > marketing) e 'deeplink' básico."""
    if not offers or "data" not in offers or not offers["data"]:
        return None
    try:
        cheapest = min(offers["data"], key=lambda x: float(x["price"]["total"]))
    except (KeyError, ValueError, TypeError):
        return None

    airline = "N/A"
    try:
        seg = cheapest["itineraries"][0]["segments"][0]
        airline = seg.get("operatingCarrierName") or seg.get("marketingCarrierName") or "N/A"
    except Exception:
        pass
    cheapest["airline"] = airline

    cheapest["deeplink"] = ""
    try:
        dep_iata = cheapest["itineraries"][0]["segments"][0]["departure"]["iataCode"]
        arr_iata = cheapest["itineraries"][0]["segments"][-1]["arrival"]["iataCode"]
        ddate = cheapest["itineraries"][0]["segments"][0]["departure"]["at"][:10]
        cheapest["deeplink"] = f"https://www.google.com/travel/flights?q=Flights%20{dep_iata}%20to%20{arr_iata}%20{ddate}"
    except Exception:
        pass

    return cheapest

# =========================
# Lógica de alerta
# =========================
def deve_alertar(preco_atual: float, melhor_anterior: float | None):
    """
    Ordem:
    1) Queda porcentual (comparada ao melhor preço observado)
    2) Teto absoluto
    """
    if melhor_anterior is not None and melhor_anterior not in (0, float("inf")):
        try:
            desconto = (melhor_anterior - preco_atual) / melhor_anterior
        except ZeroDivisionError:
            desconto = 0.0
        if desconto >= MIN_DISCOUNT_PCT:
            return True, f"queda {desconto:.1%}"

    if preco_atual <= MAX_PRECO_PP:
        return True, f"≤ teto {MAX_PRECO_PP:g}"

    return False, "sem queda / acima do teto"

# =========================
# Datas & processamento
# =========================
def gerar_datas():
    base = datetime.utcnow().date()
    datas = set()
    while len(datas) < Config.SAMPLE_DEPARTURES:
        delta = random.randint(Config.DAYS_AHEAD_FROM, Config.DAYS_AHEAD_TO)
        datas.add((base + timedelta(days=delta)).strftime("%Y-%m-%d"))
    return sorted(datas)

def process_destination(token: str, origem: str, destino: str, melhores_precos: Dict[Tuple[str, str], float]):
    log(f"🔍 {origem} → {destino}")
    key = (origem, destino)
    best = melhores_precos.get(key, float("inf"))

    for data in gerar_datas():
        time.sleep(Config.REQUEST_DELAY)
        offers = buscar_passagens(token, origem, destino, data)
        if not offers:
            continue
        cheapest = find_cheapest_offer(offers)
        if not cheapest:
            continue

        preco = float(cheapest["price"]["total"])
        moeda = cheapest["price"]["currency"]
        cia = cheapest.get("airline", "N/A")
        link = cheapest.get("deeplink", "")

        alert, motivo = deve_alertar(preco, best)
        if alert:
            msg = f"✈️ {origem} → {destino} em {data}: {preco:.2f} {moeda} ({cia}) - {motivo}."
            if link:
                msg += f"\n{link}"
            enviar_telegram(msg)

        append_history_row({
            "ts_utc": datetime.utcnow().isoformat() + "Z",
            "origem": origem,
            "destino": destino,
            "departure_date": data,
            "price_total": f"{preco:.2f}",
            "currency": moeda,
            "airline": cia,
            "deeplink": link,
            "notified": "1" if alert else "0",
            "reason": motivo,
        })

        if preco < best:
            best = preco
            melhores_precos[key] = best

def main():
    ambiente = "🚀 PRODUÇÃO" if ENV != "test" else "🔧 SANDBOX"
    log(f"Iniciando monitor | ENV={ENV} ({ambiente}) | BASE={BASE_URL}")
    token = get_token()
    log("Token obtido com sucesso.", "SUCCESS")

    melhores = load_best_prices()

    for destino in Config.DESTINOS:
        process_destination(token, Config.ORIGEM, destino, melhores)

    log("Execução finalizada.", "SUCCESS")

if __name__ == "__main__":
    main()