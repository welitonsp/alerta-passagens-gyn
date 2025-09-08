#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import time
import csv
import random
from pathlib import Path
from datetime import datetime, timedelta
import requests

# ========= util =========
def log(msg, level="INFO"):
    icons = {"INFO":"ⓘ", "SUCCESS":"✅", "ERROR":"❌", "WARNING":"⚠️"}
    print(f"[{datetime.utcnow().isoformat()}Z] {icons.get(level,' ')} {msg}")

def _truthy(x: str | None) -> bool:
    return str(x).strip().lower() in {"1","true","yes","y","on"}

# ========= config =========
class Config:
    ORIGEM  = os.getenv("ORIGEM", "GYN").strip().upper()
    DESTINOS = [d.strip().upper() for d in os.getenv(
        "DESTINOS",
        "GIG,SDU,SSA,FOR,REC,NAT,MCZ,AJU,MAO,BEL,SLZ,THE,BSB,FLN,POA,CWB,CGR,CGB,CNF,VIX,JPA,PMW,PVH,BVB,RBR,GYN,GRU,CGH"
    ).split(",") if d.strip()]
    DESTINOS = [d for d in dict.fromkeys(DESTINOS) if d != ORIGEM]  # únicos e ≠ origem

    CURRENCY = os.getenv("CURRENCY", "BRL").strip().upper()
    SAMPLE_DEPARTURES = int(os.getenv("SAMPLE_DEPARTURES", "2"))
    DAYS_AHEAD_FROM   = int(os.getenv("DAYS_AHEAD_FROM", "10"))
    DAYS_AHEAD_TO     = int(os.getenv("DAYS_AHEAD_TO", "90"))
    REQUEST_DELAY     = float(os.getenv("REQUEST_DELAY", "1.2"))
    MAX_OFFERS        = int(os.getenv("MAX_OFFERS", "5"))

    # Alertas
    MAX_PRECO_PP      = float(os.getenv("MAX_PRECO_PP", "1200"))  # teto
    MIN_DISCOUNT_PCT  = float(os.getenv("MIN_DISCOUNT_PCT", "0.25"))

    # Ida e volta
    ROUND_TRIP        = _truthy(os.getenv("ROUND_TRIP", "0"))
    STAY_NIGHTS_MIN   = int(os.getenv("STAY_NIGHTS_MIN", "5"))
    STAY_NIGHTS_MAX   = int(os.getenv("STAY_NIGHTS_MAX", "10"))

# Ambiente / endpoints
ENV = os.getenv("AMADEUS_ENV", "sandbox").strip().lower()
BASE_URL = "https://test.api.amadeus.com" if ENV in {"sandbox","test",""} else "https://api.amadeus.com"

CLIENT_ID     = os.getenv("AMADEUS_API_KEY")
CLIENT_SECRET = os.getenv("AMADEUS_API_SECRET")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# Histórico CSV
HISTORY_PATH = Path(os.getenv("HISTORY_PATH", "data/history.csv"))
HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
CSV_HEADERS = ["ts_utc","trip_type","origem","destino","departure_date","return_date","price_total","currency","airline","notified","reason"]

# ========= telegram =========
def enviar_telegram(msg: str):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        log("Telegram não configurado.", "WARNING"); return
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "HTML", "disable_web_page_preview": True},
            timeout=25
        )
        r.raise_for_status()
        log("Mensagem enviada ao Telegram.", "SUCCESS")
    except requests.RequestException as e:
        log(f"Erro Telegram: {e}", "ERROR")

# ========= amadeus =========
def get_token() -> str:
    if not CLIENT_ID or not CLIENT_SECRET:
        log("AMADEUS_API_KEY/AMADEUS_API_SECRET ausentes.", "ERROR"); sys.exit(1)
    try:
        resp = requests.post(
            f"{BASE_URL}/v1/security/oauth2/token",
            data={"grant_type": "client_credentials", "client_id": CLIENT_ID, "client_secret": CLIENT_SECRET},
            timeout=30
        )
        resp.raise_for_status()
        return resp.json()["access_token"]
    except requests.RequestException as e:
        log(f"Falha ao obter token: {e}", "ERROR")
        sys.exit(1)

def buscar_passagens(token: str, origem: str, destino: str, dep: str, ret: str | None = None):
    params = {
        "originLocationCode": origem,
        "destinationLocationCode": destino,
        "departureDate": dep,
        "adults": "1",
        "currencyCode": Config.CURRENCY,
        "max": str(Config.MAX_OFFERS),
    }
    if ret:
        params["returnDate"] = ret  # ida e volta

    try:
        r = requests.get(
            f"{BASE_URL}/v2/shopping/flight-offers",
            headers={"Authorization": f"Bearer {token}"},
            params=params,
            timeout=60
        )
        r.raise_for_status()
        return r.json()
    except requests.RequestException as e:
        log(f"Erro na busca {origem}->{destino} ({dep}{'→'+ret if ret else ''}): {e}", "ERROR")
        return None

def find_cheapest_offer(data_list: list[dict]) -> dict | None:
    if not data_list: return None
    try:
        return min(data_list, key=lambda x: float(x.get("price",{}).get("total", float("inf"))))
    except Exception:
        return None

def airline_name(offer: dict, carriers_dict: dict) -> str:
    try:
        seg = offer["itineraries"][0]["segments"][0]
        code = seg.get("operating",{}).get("carrierCode") or seg.get("carrierCode") or seg.get("marketingCarrier")
        return carriers_dict.get(code, code or "N/A")
    except Exception:
        return "N/A"

# ========= lógica =========
def deve_alertar(preco_atual: float, melhor_anterior: float | None):
    # 1) Teto absoluto
    if preco_atual <= Config.MAX_PRECO_PP:
        return True, f"≤ teto {int(Config.MAX_PRECO_PP)}"
    # 2) Queda percentual vs melhor conhecido
    if melhor_anterior not in (None, float("inf")) and melhor_anterior > 0:
        queda = (melhor_anterior - preco_atual) / melhor_anterior
        if queda >= Config.MIN_DISCOUNT_PCT:
            return True, f"queda {queda:.0%}"
    # 3) Sem alerta
    return False, "sem queda relevante"

def gerar_datas():
    """Retorna lista de (dep, ret) se ROUND_TRIP, senão [(dep, None)]"""
    today = datetime.utcnow().date()
    out = []
    used = set()
    for _ in range(Config.SAMPLE_DEPARTURES):
        offset = random.randint(Config.DAYS_AHEAD_FROM, Config.DAYS_AHEAD_TO)
        dep = (today + timedelta(days=offset)).strftime("%Y-%m-%d")
        if Config.ROUND_TRIP:
            nights = random.randint(Config.STAY_NIGHTS_MIN, Config.STAY_NIGHTS_MAX)
            ret = (today + timedelta(days=offset+nights)).strftime("%Y-%m-%d")
            key = (dep, ret)
        else:
            key = (dep, None)
        if key in used: continue
        used.add(key)
        out.append(key)
    return out

def append_history(row: dict):
    write_header = not HISTORY_PATH.exists()
    with HISTORY_PATH.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=CSV_HEADERS)
        if write_header: w.writeheader()
        w.writerow(row)

def format_msg(origem, destino, dep, ret, price, currency, cia, motivo):
    trip_flag = "IDA+VOLTA" if ret else "IDA"
    env_flag  = " <i>(SANDBOX)</i>" if ENV in {"sandbox","test",""} else ""
    rota = f"{origem} → {destino}"
    datas = f"{dep}" + (f" • volta {ret}" if ret else "")
    return (
        f"✈️ <b>{rota}</b> — <b>{trip_flag}</b>{env_flag}\n"
        f"📅 {datas}\n"
        f"💰 {price:.2f} {currency} ({cia}) — {motivo}"
    )

def process_destination(token: str, origem: str, destino: str, best_prices: dict):
    log(f"Buscando {origem} → {destino} ({'IDA+VOLTA' if Config.ROUND_TRIP else 'IDA'})")
    for dep, ret in gerar_datas():
        time.sleep(Config.REQUEST_DELAY)
        js = buscar_passagens(token, origem, destino, dep, ret)
        if not js or not js.get("data"):
            continue
        cheapest = find_cheapest_offer(js["data"])
        if not cheapest:
            continue

        carriers = js.get("dictionaries", {}).get("carriers", {})
        cia = airline_name(cheapest, carriers)
        price = float(cheapest["price"]["total"])
        currency = cheapest["price"].get("currency", Config.CURRENCY)

        key = (origem, destino, bool(ret))
        melhor_anterior = best_prices.get(key, float("inf"))
        alert, motivo = deve_alertar(price, melhor_anterior)

        msg = format_msg(origem, destino, dep, ret, price, currency, cia, motivo)
        if alert:
            enviar_telegram(msg)
            notified = "1"
        else:
            notified = "0"
            log(msg)

        append_history({
            "ts_utc": datetime.utcnow().isoformat()+"Z",
            "trip_type": "round" if ret else "oneway",
            "origem": origem,
            "destino": destino,
            "departure_date": dep,
            "return_date": ret or "",
            "price_total": f"{price:.2f}",
            "currency": currency,
            "airline": cia,
            "notified": notified,
            "reason": motivo,
        })

        if price < melhor_anterior:
            best_prices[key] = price

def load_best_prices() -> dict:
    best = {}
    if not HISTORY_PATH.exists(): return best
    try:
        with HISTORY_PATH.open("r", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                key = (r.get("origem"), r.get("destino"), (r.get("trip_type") == "round"))
                try:
                    p = float(r.get("price_total","inf"))
                except ValueError:
                    continue
                if key not in best or p < best[key]:
                    best[key] = p
    except Exception:
        pass
    return best

# ========= main =========
def main():
    log(f"Iniciando monitor | ENV={'production' if ENV not in {'sandbox','test',''} else 'sandbox'} | BASE={BASE_URL}")
    token = get_token()
    best = load_best_prices()
    for d in Config.DESTINOS:
        process_destination(token, Config.ORIGEM, d, best)
    log("Concluído.", "SUCCESS")

if __name__ == "__main__":
    main()
