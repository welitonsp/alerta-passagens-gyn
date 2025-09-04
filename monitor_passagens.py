# monitor_passagens.py
import os
import requests
import json
import sys

CLIENT_ID = os.getenv("AMADEUS_API_KEY")
CLIENT_SECRET = os.getenv("AMADEUS_API_SECRET")
ENV = os.getenv("AMADEUS_ENV", "test").lower()  # test ou prod
BASE = "https://test.api.amadeus.com" if ENV == "test" else "https://api.amadeus.com"

def get_token():
    url = f"{BASE}/v1/security/oauth2/token"
    r = requests.post(url, data={
        "grant_type": "client_credentials",
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
    }, timeout=30)
    r.raise_for_status()
    return r.json()["access_token"]

def buscar_passagens(token):
    url = f"{BASE}/v2/shopping/flight-offers"
    params = {
        "originLocationCode": "GYN",
        "destinationLocationCode": "BSB",
        "departureDate": "2025-12-15",  # ajuste conforme sua lógica
        "adults": "1",
        "currencyCode": "BRL",
        "max": "5",
    }
    headers = {"Authorization": f"Bearer {token}"}
    r = requests.get(url, headers=headers, params=params, timeout=60)
    r.raise_for_status()
    return r.json()

def main():
    if not CLIENT_ID or not CLIENT_SECRET:
        print("❌ Segredos AMADEUS_API_KEY e AMADEUS_API_SECRET não configurados.")
        sys.exit(1)

    print(f"[INFO] Ambiente: {ENV}")
    token = get_token()
    print("✅ Token obtido")

    ofertas = buscar_passagens(token)
    print(f"✅ Ofertas encontradas: {len(ofertas.get('data', []))}")
    # Aqui você pode implementar envio pro Telegram

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("❌ Erro no monitor:", e)
        sys.exit(1)