#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Monitoramento de Passagens Aéreas (Amadeus + Telegram)

- Consulta preços em múltiplos destinos.
- Envia alertas via Telegram com companhia aérea e link de busca.
- Mantém histórico em CSV para relatório diário.
"""

import os
import sys
import csv
import requests
import random
import time
from pathlib import Path
from datetime import datetime, timedelta

# =========================
# Configuração
# =========================
class Config:
    ORIGEM = os.getenv("ORIGEM", "GYN")
    DESTINOS = list(dict.fromkeys(os.getenv("DESTINOS", "SSA,FOR,REC,GRU,CGH,VCP").split(",")))
    DAYS_AHEAD_FROM = int(os.getenv("DAYS_AHEAD_FROM", "10"))
    DAYS_AHEAD_TO = int(os.getenv("DAYS_AHEAD_TO", "90"))
    SAMPLE_DEPARTURES = int(os.getenv("SAMPLE_DEPARTURES", "3"))
    CURRENCY = os.getenv("CURRENCY", "BRL")
    MAX_OFFERS = int(os.getenv("MAX_OFFERS", "5"))
    REQUEST_DELAY = float(os.getenv("REQUEST_DELAY", "1.0"))

# =========================
# Ambiente / endpoints
# =========================
CLIENT_ID = os.getenv("AMADEUS_API_KEY")
CLIENT_SECRET = os.getenv("AMADEUS_API_SECRET")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

ENV = os.getenv("AMADEUS_ENV", "sandbox").strip().lower()
BASE_URL = "https://test.api.amadeus.com" if ENV == "sandbox" else "https://api.amadeus.com"

# Histórico
HISTORY_PATH = Path("data/history.csv")
HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)

CSV_HEADERS = [
    "ts_utc", "origem", "destino", "departure_date",
    "price_total", "currency", "notified", "reason", "airline", "deeplink"
]

# =========================
# Utils
# =========================
def log(msg, level='INFO'):
    icons = {'INFO': 'ⓘ', 'SUCCESS': '✅', 'ERROR': '❌', 'WARNING': '⚠️'}
    print(f"[{datetime.utcnow().isoformat()}Z] {icons.get(level, ' ')} {msg}")

def enviar_telegram(msg: str):
    """Envia mensagem via Telegram"""
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        log("Telegram não configurado.", 'WARNING')
        return
    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            data={"chat_id": TELEGRAM_CHAT_ID, "text": msg},
            timeout=30
        )
        resp.raise_for_status()
        log("Mensagem enviada ao Telegram.", 'SUCCESS')
    except requests.RequestException as e:
        log(f"Erro ao enviar Telegram: {e}", 'ERROR')

def append_history(row):
    write_header = not HISTORY_PATH.exists()
    try:
        with HISTORY_PATH.open("a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=CSV_HEADERS)
            if write_header:
                writer.writeheader()
            writer.writerow(row)
    except Exception as e:
        log(f"Erro ao gravar histórico: {e}", "ERROR")

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

def buscar_passagens(token, origem, destino, data):
    url = f"{BASE_URL}/v2/shopping/flight-offers"
    params = {
        "originLocationCode": origem,
        "destinationLocationCode": destino,
        "departureDate": data,
        "adults": "1",
        "currencyCode": Config.CURRENCY,
        "max": Config.MAX_OFFERS,
    }
    headers = {"Authorization": f"Bearer {token}"}
    try:
        resp = requests.get(url, headers=headers, params=params, timeout=60)
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as e:
        log(f"Erro na busca {origem}->{destino} em {data}: {e}", "ERROR")
        return None

# =========================
# Lógica de ofertas
# =========================
def find_cheapest_offer(offers):
    if not offers or "data" not in offers or not offers["data"]:
        return None

    try:
        cheapest = min(
            offers["data"],
            key=lambda x: float(x.get("price", {}).get("total", float("inf")))
        )
    except (ValueError, TypeError):
        return None

    # Pega companhia aérea
    airline_name = "N/A"
    try:
        seg = cheapest["itineraries"][0]["segments"][0]
        airline_name = seg.get("operatingCarrierName") or seg.get("carrierCode") or "N/A"
    except Exception:
        pass
    cheapest["airline"] = airline_name

    return cheapest

def resumo_oferta(oferta, origem, destino, data, motivo):
    price = float(oferta.get("price", {}).get("total", 0))
    currency = oferta.get("price", {}).get("currency", "BRL")
    airline = oferta.get("airline", "N/A")
    try:
        departure_date = oferta["itineraries"][0]["segments"][0]["departure"]["at"][:10]
    except Exception:
        departure_date = data

    # link Google Flights
    deeplink = f"https://www.google.com/flights?hl=pt-BR#flt={origem}.{destino}.{departure_date}"

    msg = (
        f"✈️ {origem} → {destino} em {departure_date}: "
        f"{price:.2f} {currency} ({airline})\n"
        f"{motivo}\n🔗 {deeplink}"
    )
    return msg, deeplink

def deve_alertar(preco_atual, melhor_anterior):
    """Decide se deve alertar baseado no preço"""
    if preco_atual <= 1200:  # teto arbitrário
        return True, f"≤ teto 1200"
    if melhor_anterior is not None and melhor_anterior < float('inf'):
        desconto = (melhor_anterior - preco_atual) / melhor_anterior
        if desconto >= 0.25:
            return True, f"queda {desconto:.1%}"
    return False, "sem queda"

# =========================
# Datas e processamento
# =========================
def gerar_datas():
    hoje = datetime.utcnow().date()
    datas = []
    for _ in range(Config.SAMPLE_DEPARTURES):
        delta = random.randint(Config.DAYS_AHEAD_FROM, Config.DAYS_AHEAD_TO)
        datas.append((hoje + timedelta(days=delta)).strftime("%Y-%m-%d"))
    return datas

def process_destino(token, origem, destino, melhores_precos):
    log(f"🔎 {origem} → {destino}")
    chave = (origem, destino)
    melhor_preco = melhores_precos.get(chave, float("inf"))

    for data in gerar_datas():
        time.sleep(Config.REQUEST_DELAY)
        ofertas = buscar_passagens(token, origem, destino, data)
        cheapest = find_cheapest_offer(ofertas)

        if not cheapest:
            continue

        preco = float(cheapest["price"]["total"])
        alert, motivo = deve_alertar(preco, melhor_preco)
        notificado = False
        deeplink = ""

        if alert:
            msg, deeplink = resumo_oferta(cheapest, origem, destino, data, motivo)
            enviar_telegram(msg)
            notificado = True
            if preco < melhor_preco:
                melhores_precos[chave] = preco

        append_history({
            "ts_utc": datetime.utcnow().isoformat() + "Z",
            "origem": origem,
            "destino": destino,
            "departure_date": data,
            "price_total": f"{preco:.2f}",
            "currency": cheapest["price"]["currency"],
            "notified": "1" if notificado else "0",
            "reason": motivo,
            "airline": cheapest.get("airline", "N/A"),
            "deeplink": deeplink,
        })

# =========================
# Main
# =========================
def main():
    log(f"Iniciando monitor (ENV={ENV}, BASE={BASE_URL})")
    token = get_token()
    melhores_precos = {}

    for destino in Config.DESTINOS:
        process_destino(token, Config.ORIGEM, destino, melhores_precos)

    log("Monitoramento concluído", "SUCCESS")

if __name__ == "__main__":
    main()