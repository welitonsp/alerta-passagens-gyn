# check_amadeus_api.py
import os, sys, json, time
import requests

CLIENT_ID = os.getenv("AMADEUS_API_KEY")
CLIENT_SECRET = os.getenv("AMADEUS_API_SECRET")
ENV = os.getenv("AMADEUS_ENV", "test").lower()  # "test" (sandbox) ou "prod"

BASE = "https://test.api.amadeus.com" if ENV == "test" else "https://api.amadeus.com"
OAUTH_URL = f"{BASE}/v1/security/oauth2/token"

def die(msg, extra=None):
    print(f"[ERRO] {msg}")
    if extra is not None:
        try:
            print(json.dumps(extra, indent=2, ensure_ascii=False))
        except Exception:
            print(str(extra))
    sys.exit(1)

def get_token():
    if not CLIENT_ID or not CLIENT_SECRET:
        die("Secrets AMADEUS_API_KEY/AMADEUS_API_SECRET ausentes.")
    r = requests.post(
        OAUTH_URL,
        data={"grant_type": "client_credentials",
              "client_id": CLIENT_ID,
              "client_secret": CLIENT_SECRET},
        timeout=30,
    )
    if r.status_code != 200:
        die("Falha ao obter token (OAuth).", {"status": r.status_code, "text": r.text})
    data = r.json()
    return data["access_token"]

def call_flight_offers(token):
    # Exemplo simples: 1 adulto, ida futura. Ajuste conforme sua busca real.
    params = {
        "originLocationCode": "GYN",
        "destinationLocationCode": "BSB",
        "departureDate": "2025-12-15",
        "adults": "1",
        "currencyCode": "BRL",
        "max": "5",
    }
    url = f"{BASE}/v2/shopping/flight-offers"
    headers = {"Authorization": f"Bearer {token}"}
    r = requests.get(url, headers=headers, params=params, timeout=60)
    return r

def main():
    print(f"[INFO] Ambiente Amadeus: {ENV} | Base: {BASE}")
    token = get_token()
    print("[OK] Token obtido.")

    r = call_flight_offers(token)
    print(f"[INFO] Flight Offers status: {r.status_code}")
    if r.status_code == 200:
        # Não imprima tudo; só um resumo
        try:
            js = r.json()
            qtd = len(js.get("data", []))
            print(f"[OK] Resposta válida. Ofertas: {qtd}")
        except Exception as e:
            die("Erro ao parsear JSON de sucesso.", str(e))
    else:
        # Mostra o corpo de erro para diagnosticar (invalid_client, invalid_grant, 401, 403, 429 etc.)
        die("Chamada Flight Offers falhou.", {"status": r.status_code, "text": r.text})

if __name__ == "__main__":
    try:
        main()
    except requests.RequestException as e:
        die("Erro de rede/timeout na chamada HTTP.", str(e))