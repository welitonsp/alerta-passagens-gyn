# monitor_passagens.py
"""
Monitoramento de Passagens Aéreas (Amadeus + Telegram)

- Obtém token OAuth2 da Amadeus (sandbox ou produção).
- Consulta ofertas de voo simples (exemplo: GYN -> BSB).
- Envia resumo das ofertas encontradas via Telegram.

Requer secrets configurados no GitHub Actions:
- AMADEUS_API_KEY
- AMADEUS_API_SECRET
- TELEGRAM_BOT_TOKEN
- TELEGRAM_CHAT_ID
"""

import os
import sys
import requests
import json
from datetime import datetime

# ----------------------------------------------------------------------
# Configuração via variáveis de ambiente
# ----------------------------------------------------------------------
CLIENT_ID = os.getenv("AMADEUS_API_KEY")
CLIENT_SECRET = os.getenv("AMADEUS_API_SECRET")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
ENV = os.getenv("AMADEUS_ENV", "test").lower()  # "test" ou "prod"

BASE = "https://test.api.amadeus.com" if ENV == "test" else "https://api.amadeus.com"


# ----------------------------------------------------------------------
# Funções auxiliares
# ----------------------------------------------------------------------
def log(msg):
    """Log simples com timestamp."""
    print(f"[{datetime.now().isoformat()}] {msg}")


def get_token():
    """Autentica na Amadeus e retorna o access_token."""
    if not CLIENT_ID or not CLIENT_SECRET:
        log("❌ Segredos AMADEUS_API_KEY e AMADEUS_API_SECRET não configurados.")
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
        log(f"❌ Falha ao obter token: {resp.status_code} {resp.text}")
        sys.exit(1)

    return resp.json()["access_token"]


def buscar_passagens(token, origem="GYN", destino="BSB", data="2025-12-15"):
    """Consulta ofertas de voo simples na Amadeus."""
    url = f"{BASE}/v2/shopping/flight-offers"
    params = {
        "originLocationCode": origem,
        "destinationLocationCode": destino,
        "departureDate": data,
        "adults": "1",
        "currencyCode": "BRL",
        "max": "5",
    }
    headers = {"Authorization": f"Bearer {token}"}
    resp = requests.get(url, headers=headers, params=params, timeout=60)

    if resp.status_code != 200:
        log(f"❌ Erro na busca de passagens: {resp.status_code} {resp.text}")
        sys.exit(1)

    return resp.json()


def formatar_ofertas(ofertas: dict) -> str:
    """Gera resumo textual das ofertas."""
    data = ofertas.get("data", [])
    if not data:
        return "Nenhuma oferta encontrada."

    linhas = ["✈️ Ofertas de voo encontradas:"]
    for i, oferta in enumerate(data, start=1):
        preco = oferta["price"]["total"]
        moeda = oferta["price"]["currency"]
        itinerario = oferta["itineraries"][0]["segments"][0]
        origem = itinerario["departure"]["iataCode"]
        destino = itinerario["arrival"]["iataCode"]
        partida = itinerario["departure"]["at"][:10]
        linhas.append(f"{i}. {origem} → {destino} em {partida} | {preco} {moeda}")

    return "\n".join(linhas)


def enviar_telegram(msg: str):
    """Envia mensagem via Telegram."""
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        log("⚠️ Telegram não configurado (BOT_TOKEN/CHAT_ID ausentes).")
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    resp = requests.post(
        url,
        data={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "Markdown"},
        timeout=30,
    )

    if resp.status_code != 200:
        log(f"❌ Erro ao enviar Telegram: {resp.status_code} {resp.text}")
    else:
        log("✅ Mensagem enviada ao Telegram.")


# ----------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------
def main():
    log(f"Iniciando monitor de passagens (ENV={ENV}, BASE={BASE})")

    token = get_token()
    log("✅ Token obtido com sucesso.")

    ofertas = buscar_passagens(token)
    resumo = formatar_ofertas(ofertas)
    log("Resumo das ofertas:\n" + resumo)

    enviar_telegram(resumo)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"❌ Erro inesperado: {e}")
        sys.exit(1)