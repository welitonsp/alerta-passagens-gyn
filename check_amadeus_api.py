#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
check_amadeus_api.py
Diagnóstico rápido:
- valida envs
- obtém token
- faz 1 busca simples (GET) e mostra status/contagem
"""

import os, sys
from datetime import date, timedelta
import requests

API_KEY = os.getenv("AMADEUS_API_KEY")
API_SECRET = os.getenv("AMADEUS_API_SECRET")

ENV = (os.getenv("AMADEUS_ENV", "sandbox") or "sandbox").strip().lower()
BASE = os.getenv("AMADEUS_BASE_URL") or (
    "https://test.api.amadeus.com" if ENV in ("sandbox", "test") else "https://api.amadeus.com"
)

ORIGIN  = os.getenv("ORIGIN", "GYN")
DEST    = os.getenv("DEST", "BSB")
DATE    = os.getenv("DATE") or (date.today() + timedelta(days=60)).isoformat()
CURRENCY= os.getenv("CURRENCY", "BRL")
MAX     = os.getenv("MAX", "5")

def die(msg, extra=None):
    print("[ERRO]", msg)
    if extra:
        print(extra if isinstance(extra, str) else str(extra))
    sys.exit(1)

def main():
    print(f"[INFO] ENV={ENV} BASE={BASE}")
    if not API_KEY or not API_SECRET:
        die("AMADEUS_API_KEY/AMADEUS_API_SECRET ausentes.")

    # OAuth
    url = f"{BASE}/v1/security/oauth2/token"
    r = requests.post(url, data={
        "grant_type":"client_credentials",
        "client_id": API_KEY,
        "client_secret": API_SECRET,
    }, timeout=25)
    print("[INFO] OAuth status:", r.status_code)
    if r.status_code != 200:
        die("Falha OAuth", r.text[:400])
    token = r.json().get("access_token")
    if not token:
        die("OAuth sem access_token", r.text[:400])
    print("[OK] Token recebido.")

    # Flight offers (GET)
    fo = f"{BASE}/v2/shopping/flight-offers"
    r2 = requests.get(fo, headers={"Authorization": f"Bearer {token}"}, params={
        "originLocationCode": ORIGIN,
        "destinationLocationCode": DEST,
        "departureDate": DATE,
        "adults": "1",
        "currencyCode": CURRENCY,
        "max": MAX,
    }, timeout=40)
    print("[INFO] Flight Offers status:", r2.status_code)
    if r2.status_code != 200:
        die("Falha Flight Offers", r2.text[:600])
    try:
        data = r2.json()
        qtd = len(data.get("data", []))
        print(f"[OK] Ofertas: {qtd}")
    except Exception as e:
        die("JSON inválido no Flight Offers", str(e))

if __name__ == "__main__":
    try:
        main()
    except requests.RequestException as e:
        die("Erro de rede", str(e))