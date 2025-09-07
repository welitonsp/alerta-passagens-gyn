#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Monitoramento de Passagens Aéreas (Amadeus + Telegram)

- Configuração via classe Config (origem, destinos, parâmetros).
- Autenticação OAuth2 na Amadeus (Self-Service).
- Busca ofertas de voo e identifica a mais barata em datas dinâmicas.
- Envia resumo via Telegram.

Requer segredos no GitHub Actions:
- AMADEUS_API_KEY
- AMADEUS_API_SECRET
- TELEGRAM_BOT_TOKEN
- TELEGRAM_CHAT_ID
"""

import os
import sys
import requests
import random
from datetime import datetime, timedelta

# ----------------------------------------------------------------------
# Classe de configuração
# ----------------------------------------------------------------------
class Config:
    ORIGEM = "GYN"
    DESTINOS = ["SSA", "FOR", "REC", "GRU", "CGH", "VCP"]
    # Elimina duplicados preservando ordem
    DESTINOS = list(dict.fromkeys(DESTINOS))

    DAYS_AHEAD_FROM = 10
    DAYS_AHEAD_TO = 90
    SAMPLE_DEPARTURES = 3   # quantas datas testar por destino
    CURRENCY = "BRL"
    MAX_OFFERS = 5          # limite de ofertas por busca

# ----------------------------------------------------------------------
# Configurações de ambiente (corrigido)
# ----------------------------------------------------------------------
CLIENT_ID = os.getenv("AMADEUS_API_KEY")
CLIENT_SECRET = os.getenv("AMADEUS_API_SECRET")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID") or os.getenv("CHAT_ID")

# aceita 'sandbox'/'test' e 'production'/'prod'
ENV = (os.getenv("AMADEUS_ENV", "sandbox") or "sandbox").strip().lower()

# permite forçar via AMADEUS_BASE_URL; senão decide pela ENV
BASE = os.getenv("AMADEUS_BASE_URL") or (
    "https://test.api.amadeus.com" if ENV in ("sandbox", "test") else "https://api.amadeus.com"
)

# ----------------------------------------------------------------------
# Utilitários de log
# ----------------------------------------------------------------------
def log(msg, level='INFO'):
    indicators = {'INFO': 'ⓘ', 'SUCCESS': '✅', 'ERROR': '❌', 'WARNING': '⚠️'}
    indicator = indicators.get(level.upper(), ' ')
    print(f"[{datetime.utcnow().isoformat()}Z] {indicator} {msg}")

# ----------------------------------------------------------------------
# Amadeus API
# ----------------------------------------------------------------------
def get_token():
    """Obtém token OAuth2 da Amadeus."""
    if not CLIENT_ID or not CLIENT_SECRET:
        log("Segredos AMADEUS_API_KEY e/ou AMADEUS_API_SECRET não configurados.", 'ERROR')
        sys.exit(1)

    url = f"{BASE}/v1/security/oauth2/token"
    resp = requests.post(
        url,
        data={
            "grant_type": "client_credentials",
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
        },
        timeout=30,
    )
    if resp.status_code != 200:
        log(f"Falha ao obter token: {resp.status_code} {resp.text}", 'ERROR')
        sys.exit(1)

    return resp.json()["access_token"]

def buscar_passagens(token, origem, destino, data):
    """Consulta ofertas de voo na Amadeus (GET simples)."""
    url = f"{BASE}/v2/shopping/flight-offers"
    params = {
        "originLocationCode": origem,
        "destinationLocationCode": destino,
        "departureDate": data,
        "adults": "1",
        "currencyCode": Config.CURRENCY,
        "max": str(Config.MAX_OFFERS),
    }
    headers = {"Authorization": f"Bearer {token}"}
    resp = requests.get(url, headers=headers, params=params, timeout=60)
    if resp.status_code != 200:
        log(f"Erro na busca {origem}->{destino} {data}: {resp.status_code} {resp.text}", 'ERROR')
        return None
    return resp.json()

def find_cheapest_offer(offers):
    """Encontra a oferta mais barata em uma resposta 'data'."""
    if not offers or "data" not in offers or not offers["data"]:
        return None
    try:
        return min(offers["data"], key=lambda x: float(x["price"]["total"]))
    except Exception:
        return None

def format_cheapest_offer(cheapest_offer, origem, destino, data):
    """Formata mensagem para a oferta mais barata."""
    if not cheapest_offer:
        return f"❌ Nenhuma oferta encontrada para {origem} → {destino} em {data}."
    price = float(cheapest_offer["price"]["total"])
    currency = cheapest_offer["price"]["currency"]
    # data de partida do primeiro segmento do primeiro itinerário
    try:
        departure_date = cheapest_offer["itineraries"][0]["segments"][0]["departure"]["at"][:10]
    except Exception:
        departure_date = data
    return f"✈️ {origem} → {destino} em {departure_date}: {price:.2f} {currency} (menor preço)."

# ----------------------------------------------------------------------
# Telegram
# ----------------------------------------------------------------------
def enviar_telegram(msg: str):
    """Envia mensagem via Telegram (silenciosamente se faltarem variáveis)."""
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        log("Telegram não configurado (BOT_TOKEN/CHAT_ID).", 'WARNING')
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    resp = requests.post(url, data={"chat_id": TELEGRAM_CHAT_ID, "text": msg}, timeout=30)
    if resp.status_code != 200:
        log(f"Erro ao enviar Telegram: {resp.status_code} {resp.text}", 'ERROR')
    else:
        log("Mensagem enviada ao Telegram.", 'SUCCESS')

# ----------------------------------------------------------------------
# Datas de busca
# ----------------------------------------------------------------------
def gerar_datas():
    """Gera lista de datas dentro do intervalo configurado."""
    hoje = datetime.utcnow()
    datas = set()
    # evita datas repetidas
    for _ in range(max(1, Config.SAMPLE_DEPARTURES * 2)):
        if len(datas) >= Config.SAMPLE_DEPARTURES:
            break
        delta = random.randint(Config.DAYS_AHEAD_FROM, Config.DAYS_AHEAD_TO)
        datas.add((hoje + timedelta(days=delta)).strftime("%Y-%m-%d"))
    return sorted(datas)

# ----------------------------------------------------------------------
# Execução por destino
# ----------------------------------------------------------------------
def process_destination(token, origem, destino):
    log(f"🔎 Buscando voos {origem} → {destino}...")
    melhores = []
    for data in gerar_datas():
        ofertas = buscar_passagens(token, origem, destino, data)
        cheapest = find_cheapest_offer(ofertas)
        if cheapest:
            melhores.append((cheapest, data))
    if not melhores:
        msg = f"❌ Nenhuma oferta encontrada para {origem} → {destino} nas datas testadas."
        log(msg)
        enviar_telegram(msg)
        return
    oferta_barata, data = min(melhores, key=lambda x: float(x[0]["price"]["total"]))
    resumo = format_cheapest_offer(oferta_barata, origem, destino, data)
    log(resumo)
    enviar_telegram(resumo)

# ----------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------
def main():
    log(f"Iniciando monitor (ENV={ENV}, BASE={BASE})")
    try:
        token = get_token()
        log("Token obtido com sucesso.", 'SUCCESS')
        for destino in Config.DESTINOS:
            process_destination(token, Config.ORIGEM, destino)
        log("Execução do monitor finalizada.", 'SUCCESS')
    except SystemExit:
        raise
    except Exception as e:
        log(f"Erro inesperado: {e}", 'ERROR')
        sys.exit(1)

if __name__ == "__main__":
    main()