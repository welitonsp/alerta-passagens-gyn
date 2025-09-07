#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import csv
import time
import random
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, Tuple, Any, Optional

import requests

# ============================================================
# Capitais (IATA) — lista final, sem duplicatas
# ============================================================
CAPITAIS_BR = [
    # Norte
    "BEL","MAO","MCP","PVH","BVB","RBR","PMW",
    # Nordeste
    "SLZ","FOR","NAT","JPA","REC","AJU","SSA","THE","MCZ",
    # Centro-Oeste
    "BSB","CGB","CGR","GYN",
    # Sudeste
    "VIX","CNF","GIG","SDU","GRU","CGH","VCP",
    # Sul
    "FLN","CWB","POA",
]
ALIASES = {
    "MACE": "MCZ",
    "PAL": "PMW",
    "RIO": "GIG",
    "SAO": "GRU",
}

def normalize_caps(lista):
    out = []
    for x in lista:
        x = x.strip().upper()
        out.append(ALIASES.get(x, x))
    seen = set(); dedup = []
    for x in out:
        if x and x not in seen:
            dedup.append(x); seen.add(x)
    return dedup

class Config:
    ORIGEM = os.getenv("ORIGEM", "GYN").strip().upper()
    _dest_env = os.getenv("DESTINOS", "SSA,FOR,REC,GRU,CGH,VCP").strip()
    DESTINOS = normalize_caps(CAPITAIS_BR if _dest_env.upper()=="CAPITAIS_BR"
                              else _dest_env.split(","))
    DAYS_AHEAD_FROM = int(os.getenv("DAYS_AHEAD_FROM", "10"))
    DAYS_AHEAD_TO   = int(os.getenv("DAYS_AHEAD_TO", "90"))
    SAMPLE_DEPARTURES = int(os.getenv("SAMPLE_DEPARTURES", "3"))
    CURRENCY = os.getenv("CURRENCY", "BRL").strip().upper()
    MAX_OFFERS = int(os.getenv("MAX_OFFERS", "5"))
    REQUEST_DELAY = float(os.getenv("REQUEST_DELAY", "2.2"))  # produção: mais cautela

# Alertas
MAX_PRECO_PP = float(os.getenv("MAX_PRECO_PP", "999999"))
MIN_DISCOUNT_PCT = float(os.getenv("MIN_DISCOUNT_PCT", "0.10"))

# Ambiente / endpoints
ENV = (os.getenv("AMADEUS_ENV", "production") or "production").strip().lower()
BASE = os.getenv("AMADEUS_BASE_URL") or (
    "https://test.api.amadeus.com" if ENV in ("sandbox", "test") else "https://api.amadeus.com"
)
CLIENT_ID = os.getenv("AMADEUS_API_KEY")
CLIENT_SECRET = os.getenv("AMADEUS_API_SECRET")

# Telegram
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID") or os.getenv("CHAT_ID")

# Histórico
HISTORY_PATH = Path(os.getenv("HISTORY_PATH", "data/history.csv"))
HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
CSV_HEADERS = [
    "ts_utc","origem","destino","departure_date",
    "price_total","currency","notified","reason","airline"
]

# HTTP – backoff simples
RETRY_STATUS = {429, 500, 502, 503, 504}
MAX_RETRIES = int(os.getenv("MAX_RETRIES", "3"))
BACKOFF_BASE = float(os.getenv("BACKOFF_BASE", "1.7"))

# Cache (TTL em segundos)
CACHE_TTL = int(os.getenv("CACHE_TTL", "300"))
_cache: Dict[Tuple[str,str,str], Tuple[float, Any]] = {}

# ============================================================
# Utilitários
# ============================================================
def log(msg, level="INFO"):
    icons = {"INFO":"ⓘ","SUCCESS":"✅","ERROR":"❌","WARNING":"⚠️"}
    print(f"[{datetime.utcnow().isoformat()}Z] {icons.get(level,' ')} {msg}")

def _http_with_backoff(method, url, **kw):
    for i in range(1, MAX_RETRIES+1):
        try:
            r = requests.request(method, url, timeout=kw.pop("timeout", 30), **kw)
            if r.status_code in RETRY_STATUS:
                raise requests.HTTPError(f"status {r.status_code}: {r.text[:180]}")
            return r
        except requests.RequestException as e:
            if i == MAX_RETRIES:
                raise
            sleep = BACKOFF_BASE ** i
            log(f"HTTP erro ({e}). retry {i}/{MAX_RETRIES} em {sleep:.1f}s", "WARNING")
            time.sleep(sleep)
    raise RuntimeError("unreachable")

def enviar_telegram(msg: str):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        log("Telegram não configurado (BOT_TOKEN/CHAT_ID).", "WARNING"); return
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            data={"chat_id": TELEGRAM_CHAT_ID, "text": msg}, timeout=30
        )
        r.raise_for_status()
        log("Mensagem enviada ao Telegram.", "SUCCESS")
    except requests.RequestException as e:
        log(f"Erro ao enviar Telegram: {e}", "ERROR")

# ============================================================
# Histórico
# ============================================================
def load_best_prices():
    best = {}
    if not HISTORY_PATH.exists(): return best
    try:
        with HISTORY_PATH.open("r", encoding="utf-8") as f:
            rd = csv.DictReader(f)
            for row in rd:
                try:
                    key = (row["origem"], row["destino"])
                    p = float(row["price_total"])
                    if key not in best or p < best[key]:
                        best[key] = p
                except Exception:
                    continue
    except Exception as e:
        log(f"Falha lendo histórico: {e}", "WARNING")
    return best

def append_history_row(data: dict):
    write_header = not HISTORY_PATH.exists()
    try:
        with HISTORY_PATH.open("a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=CSV_HEADERS)
            if write_header:
                w.writeheader()
            w.writerow(data)
    except IOError as e:
        log(f"Erro ao gravar histórico: {e}", "ERROR")

# ============================================================
# Amadeus
# ============================================================
def get_token() -> str:
    if not CLIENT_ID or not CLIENT_SECRET:
        log("AMADEUS_API_KEY/AMADEUS_API_SECRET ausentes.", "ERROR")
        sys.exit(1)
    url = f"{BASE}/v1/security/oauth2/token"
    r = _http_with_backoff("POST", url, data={
        "grant_type":"client_credentials","client_id":CLIENT_ID,"client_secret":CLIENT_SECRET
    })
    if r.status_code != 200:
        log(f"Falha OAuth: {r.status_code} {r.text[:200]}", "ERROR"); sys.exit(1)
    return r.json().get("access_token","")

def buscar_passagens(token: str, origem: str, destino: str, data: str) -> Optional[dict]:
    url = f"{BASE}/v2/shopping/flight-offers"
    params = {
        "originLocationCode": origem,
        "destinationLocationCode": destino,
        "departureDate": data,
        "adults": "1",
        "currencyCode": Config.CURRENCY,
        "max": str(Config.MAX_OFFERS),
    }
    hdr = {"Authorization": f"Bearer {token}"}
    try:
        r = _http_with_backoff("GET", url, params=params, headers=hdr, timeout=60)
        if r.status_code != 200:
            log(f"Erro busca {origem}->{destino} {data}: {r.status_code} {r.text[:200]}", "ERROR")
            return None
        return r.json()
    except requests.RequestException as e:
        log(f"HTTP falhou {origem}->{destino} {data}: {e}", "ERROR")
        return None

def cached_buscar_passagens(token, origem, destino, data):
    key = (origem, destino, data)
    now = time.time()
    hit = _cache.get(key)
    if hit and (now - hit[0] <= CACHE_TTL):
        return hit[1]
    out = buscar_passagens(token, origem, destino, data)
    _cache[key] = (now, out)
    return out

# ============================================================
# Lógica de preços / mensagens
# ============================================================
def find_cheapest_offer(offers):
    if not offers or "data" not in offers or not offers["data"]:
        log("Aviso: Nenhuma oferta encontrada.", "WARNING"); return None
    try:
        cheapest = min(
            offers["data"],
            key=lambda x: float(x.get("price", {}).get("total", float("inf")))
        )
    except (ValueError, TypeError) as e:
        log(f"Erro ao processar preços: {e}", "ERROR"); return None

    airline_name = "N/A"
    try:
        segments = cheapest["itineraries"][0]["segments"]
        operating_carrier = segments[0].get("operatingCarrierName")
        marketing_carrier = segments[0].get("marketingCarrierName")
        airline_name = operating_carrier or marketing_carrier or "N/A"
    except (KeyError, IndexError):
        log("Aviso: Dados da companhia aérea não disponíveis.", "WARNING")

    cheapest["airline"] = airline_name
    return cheapest

def resumo_oferta(oferta, origem, destino, data, motivo):
    price = float(oferta.get("price", {}).get("total", 0))
    currency = oferta.get("price", {}).get("currency", Config.CURRENCY)
    airline = oferta.get("airline", "N/A")
    try:
        departure_date = oferta["itineraries"][0]["segments"][0]["departure"]["at"][:10]
    except (KeyError, IndexError):
        departure_date = data
    return (
        f"✈️ {origem} → {destino} em {departure_date}: "
        f"{price:.2f} {currency} ({airline}) - menor preço, {motivo}."
    )

def append_history(origem, destino, data, cheapest, notified, motivo):
    if not cheapest:
        log("Aviso: Nenhuma oferta válida para salvar no histórico.", "WARNING"); return
    try:
        price = float(cheapest.get("price", {}).get("total", 0))
        currency = cheapest.get("price", {}).get("currency", Config.CURRENCY)
        airline = cheapest.get("airline", "N/A")
    except (ValueError, TypeError):
        log("Erro ao converter valores numéricos para histórico.", "ERROR"); return

    row = {
        "ts_utc": datetime.utcnow().isoformat() + "Z",
        "origem": origem,
        "destino": destino,
        "departure_date": data,
        "price_total": f"{price:.2f}",
        "currency": currency,
        "notified": "1" if notified else "0",
        "reason": motivo,
        "airline": airline
    }
    append_history_row(row)

def gerar_datas_estrategicas(d_from: int, d_to: int, n: int):
    base = datetime.utcnow().date()
    choices = set()
    for _ in range(max(1, n*2)):
        if len(choices) >= n: break
        delta = random.randint(d_from, d_to)
        choices.add((base + timedelta(days=delta)).strftime("%Y-%m-%d"))
    return sorted(choices)

def deve_alertar(preco_atual: float, melhor_anterior: Optional[float]):
    if preco_atual <= MAX_PRECO_PP:
        return True, f"≤ teto {MAX_PRECO_PP:.2f}"
    if (melhor_anterior is not None) and melhor_anterior < float("inf"):
        drop = (melhor_anterior - preco_atual) / melhor_anterior
        if drop >= MIN_DISCOUNT_PCT:
            return True, f"queda {drop*100:.1f}% vs {melhor_anterior:.2f}"
    return False, "sem queda/teto"

# ============================================================
# Pipeline por destino
# ============================================================
def process_destination(token, origem, destino, best_prices):
    log(f"🔍 {origem} → {destino}")
    try:
        datas = gerar_datas_estrategicas(
            Config.DAYS_AHEAD_FROM, Config.DAYS_AHEAD_TO, Config.SAMPLE_DEPARTURES
        )
    except Exception as e:
        log(f"Erro gerando datas: {e}", "ERROR"); return

    melhor_global = best_prices.get((origem, destino), float("inf"))

    for data in datas:
        try:
            time.sleep(Config.REQUEST_DELAY)
            ofertas = cached_buscar_passagens(token, origem, destino, data)
            cheapest = find_cheapest_offer(ofertas)

            if not cheapest:
                log(f"Nenhum voo encontrado para {data}", "WARNING")
                continue

            price = float(cheapest["price"]["total"])
            alert, motivo = deve_alertar(price, melhor_global)

            notified = False
            if alert:
                enviar_telegram(resumo_oferta(cheapest, origem, destino, data, motivo))
                notified = True

            append_history(origem, destino, data, cheapest, notified, motivo)

            if price < melhor_global:
                melhor_global = price
                best_prices[(origem, destino)] = price

        except requests.exceptions.Timeout:
            log(f"Timeout buscando passagens para {data}", "WARNING")
        except requests.exceptions.ConnectionError:
            log("Falha de conexão com a API", "ERROR"); break
        except Exception as e:
            log(f"Erro inesperado processando {data}: {e}", "ERROR")

# ============================================================
# Main
# ============================================================
def main():
    log(f"Iniciando monitor (ENV={ENV}, BASE={BASE})")
    try:
        token = get_token()
        log("Token obtido com sucesso.", "SUCCESS")
        best_prices = load_best_prices()
        for destino in Config.DESTINOS:
            if destino == Config.ORIGEM:
                continue
            process_destination(token, Config.ORIGEM, destino, best_prices)
        log("Monitoramento concluído.", "SUCCESS")
    except SystemExit:
        raise
    except Exception as e:
        log(f"Erro inesperado: {e}", "ERROR")
        sys.exit(1)

if __name__ == "__main__":
    main()