"""
Monitoramento de Passagens Aéreas (Amadeus + Telegram)

- Configuração via classe Config (origem, destinos, parâmetros).
- Autenticação OAuth2 na Amadeus.
- Busca ofertas de voo e identifica a mais barata.
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
from datetime import datetime


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
    STAY_NIGHTS_MIN = 5
    STAY_NIGHTS_MAX = 10
    SAMPLE_DEPARTURES = 3
    SAMPLE_STAYS = 2
    MAX_PRECO_PP = 1200
    MIN_DISCOUNT_PCT = 0.25
    MIN_DAYDROP_PCT = 0.30
    BIN_SIZE_DAYS = 7
    MAX_PER_DEST = 1
    MAX_STOPOVERS = 1
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
        log(f"Erro na busca de {origem}->{destino}: {resp.status_code} {resp.text}", 'ERROR')
        return None

    return resp.json()

def find_cheapest_offer(offers):
    """Encontra a oferta mais barata em uma lista de ofertas da Amadeus."""
    if not offers or "data" not in offers:
        return None
    
    cheapest = min(offers["data"], key=lambda x: float(x["price"]["total"]))
    return cheapest

def format_cheapest_offer(cheapest_offer, origem, destino):
    """Formata uma mensagem concisa para a oferta mais barata."""
    if not cheapest_offer:
        return f"❌ Nenhuma oferta encontrada para {origem} → {destino}."

    price = float(cheapest_offer["price"]["total"])
    currency = cheapest_offer["price"]["currency"]
    
    # Obtém a data de partida do primeiro segmento do primeiro itinerário
    departure_date = cheapest_offer["itineraries"][0]["segments"][0]["departure"]["at"][:10]

    return f"✈️ Oferta mais barata {origem} → {destino}: {price:.2f} {currency} em {departure_date}."

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
def process_destination(token, origem, destino, departure_date):
    """Processa um único destino e envia uma mensagem via Telegram."""
    log(f"Buscando voos para {destino}...")
    ofertas = buscar_passagens(token, origem, destino, departure_date)
    cheapest = find_cheapest_offer(ofertas)
    resumo = format_cheapest_offer(cheapest, origem, destino)
    log(resumo)
    enviar_telegram(resumo)

def main():
    log(f"Iniciando monitor (ENV={ENV}, BASE={BASE})")

    try:
        token = get_token()
        log("Token obtido com sucesso.", 'SUCCESS')

        # Substitua '2025-12-15' por uma data dinâmica se necessário
        departure_date = "2025-12-15" 

        for destino in Config.DESTINOS:
            process_destination(token, Config.ORIGEM, destino, departure_date)
            
        log("Execução do monitor finalizada.", 'SUCCESS')

    except Exception as e:
        log(f"Erro inesperado: {e}", 'ERROR')
        sys.exit(1)


if __name__ == "__main__":
    main()

