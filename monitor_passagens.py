"""
Monitoramento de Passagens Aéreas (Amadeus + Telegram)

- Configuração via classe Config (origem, destinos, parâmetros).
- Autenticação OAuth2 na Amadeus.
- Busca ofertas de voo e identifica a mais barata em datas dinâmicas.
- Envia resumo via Telegram.

Requer segredos configurados no GitHub Actions:
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


# ----------------------------------------------------------------------
# Configurações de ambiente
# ----------------------------------------------------------------------
CLIENT_ID = os.getenv("AMADEUS_API_KEY")
CLIENT_SECRET = os.getenv("AMADEUS_API_SECRET")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
ENV = os.getenv("AMADEUS_ENV", "test").lower()  # test ou prod

BASE = "https://test.api.amadeus.com" if ENV == "test" else "https://api.amadeus.com"


def log(msg, level='INFO'):
    """Log com timestamp e indicador de nível."""
    indicators = {
        'INFO': 'ⓘ',
        'SUCCESS': '✅',
        'ERROR': '❌',
        'WARNING': '⚠️'
    }
    indicator = indicators.get(level.upper(), ' ')
    print(f"[{datetime.now().isoformat()}] {indicator} {msg}")


# ----------------------------------------------------------------------
# Amadeus API
# ----------------------------------------------------------------------
def get_token():
    """Obtém token OAuth2 da Amadeus."""
    if not CLIENT_ID or not CLIENT_SECRET:
        log("Segredos AMADEUS_API_KEY e AMADEUS_API_SECRET não configurados.", 'ERROR')
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
    """Consulta ofertas de voo na Amadeus."""
    url = f"{BASE}/v2/shopping/flight-offers"
    params = {
        "originLocationCode": origem,
        "destinationLocationCode": destino,
        "departureDate": data,
        "adults": "1",
        "currencyCode": Config.CURRENCY,
        "max": "5",
    }
    headers = {"Authorization": f"Bearer {token}"}
    resp = requests.get(url, headers=headers, params=params, timeout=60)

    if resp.status_code != 200:
        log(f"Erro na busca de {origem}->{destino} em {data}: {resp.status_code} {resp.text}", 'ERROR')
        return None

    return resp.json()


def find_cheapest_offer(offers):
    """Encontra a oferta mais barata em uma lista de ofertas da Amadeus."""
    if not offers or "data" not in offers or not offers["data"]:
        return None
    return min(offers["data"], key=lambda x: float(x["price"]["total"]))


def format_cheapest_offer(cheapest_offer, origem, destino, data):
    """Formata mensagem concisa para a oferta mais barata."""
    if not cheapest_offer:
        return f"❌ Nenhuma oferta encontrada para {origem} → {destino} em {data}."

    price = float(cheapest_offer["price"]["total"])
    currency = cheapest_offer["price"]["currency"]

    departure_date = cheapest_offer["itineraries"][0]["segments"][0]["departure"]["at"][:10]
    return f"✈️ Mais barato {origem} → {destino} em {departure_date}: {price:.2f} {currency}."


# ----------------------------------------------------------------------
# Telegram
# ----------------------------------------------------------------------
def enviar_telegram(msg: str):
    """Envia mensagem via Telegram."""
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        log("Telegram não configurado.", 'WARNING')
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    resp = requests.post(
        url,
        data={"chat_id": TELEGRAM_CHAT_ID, "text": msg},
        timeout=30,
    )

    if resp.status_code != 200:
        log(f"Erro ao enviar Telegram: {resp.status_code} {resp.text}", 'ERROR')
    else:
        log("Mensagem enviada ao Telegram.", 'SUCCESS')


# ----------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------
def gerar_datas():
    """Gera lista de datas dentro do intervalo configurado."""
    hoje = datetime.today()
    datas = []
    for _ in range(Config.SAMPLE_DEPARTURES):
        delta = random.randint(Config.DAYS_AHEAD_FROM, Config.DAYS_AHEAD_TO)
        datas.append((hoje + timedelta(days=delta)).strftime("%Y-%m-%d"))
    return datas


def process_destination(token, origem, destino):
    """Processa destino para várias datas e envia a mais barata."""
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

    # Pega o mais barato de todas as datas testadas
    oferta_barata, data = min(melhores, key=lambda x: float(x[0]["price"]["total"]))
    resumo = format_cheapest_offer(oferta_barata, origem, destino, data)
    log(resumo)
    enviar_telegram(resumo)


def main():
    log(f"Iniciando monitor (ENV={ENV}, BASE={BASE})")

    try:
        token = get_token()
        log("Token obtido com sucesso.", 'SUCCESS')

        for destino in Config.DESTINOS:
            process_destination(token, Config.ORIGEM, destino)

        log("Execução do monitor finalizada.", 'SUCCESS')

    except Exception as e:
        log(f"Erro inesperado: {e}", 'ERROR')
        sys.exit(1)


if __name__ == "__main__":
    main()