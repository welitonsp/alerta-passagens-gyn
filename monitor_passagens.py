#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Monitoramento de Passagens Aéreas (Amadeus + Telegram) — SANDBOX por padrão.

Este módulo expõe as funções que os testes usam:
- get_token()
- deve_alertar(preco_atual, melhor_anterior)

E também implementa o monitor completo (main) com histórico CSV e envio ao Telegram.
"""

# ===================== IMPORTS =====================
import os
import sys
import time
import csv
import random
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Tuple, Optional, Dict, Any
import requests

# ===================== LOG =========================
def log(msg: str, level: str = "INFO") -> None:
    icons = {"INFO": "ⓘ", "SUCCESS": "✅", "ERROR": "❌", "WARNING": "⚠️"}
    print(f"[{datetime.utcnow().isoformat()}Z] {icons.get(level,' ')} {msg}")

# ===================== DESTINOS HELPERS ============
def brazil_capitals_iata() -> List[str]:
    # Capitais + hubs comuns
    return [
        "GIG","SDU","SSA","FOR","REC","NAT","MCZ","AJU",
        "MAO","BEL","SLZ","THE","BSB","FLN","POA","CWB",
        "CGR","CGB","CNF","VIX","JPA","PMW","PVH","BVB",
        "RBR","GYN","GRU","CGH"
    ]

def dedupe_keep_order(seq: List[str]) -> List[str]:
    seen, out = set(), []
    for x in seq:
        x = x.strip().upper()
        if x and x not in seen:
            seen.add(x); out.append(x)
    return out

# ===================== CONFIG ======================
class Config:
    ORIGEM = os.getenv("ORIGEM", "GYN").strip().upper()
    _dests_str = os.getenv("DESTINOS", ",".join(brazil_capitals_iata()))
    DESTINOS = [x.strip().upper() for x in _dests_str.split(",") if x.strip()]

    DAYS_AHEAD_FROM = int(os.getenv("DAYS_AHEAD_FROM", "10"))
    DAYS_AHEAD_TO   = int(os.getenv("DAYS_AHEAD_TO", "90"))
    SAMPLE_DEPARTURES = int(os.getenv("SAMPLE_DEPARTURES", "2"))
    MAX_OFFERS = int(os.getenv("MAX_OFFERS", "5"))
    CURRENCY = os.getenv("CURRENCY", "BRL")
    REQUEST_DELAY = float(os.getenv("REQUEST_DELAY", "1.2"))

# remover a origem da lista e deduplicar (fora da classe)
Config.DESTINOS = dedupe_keep_order([d for d in Config.DESTINOS if d != Config.ORIGEM])

# ===================== AMBIENTE/ENDPOINTS ==========
# Por padrão SANDBOX (teste). Só use production se tiver credenciais pagas.
ENV = os.getenv("AMADEUS_ENV", "sandbox").strip().lower()
BASE_URL = os.getenv("AMADEUS_BASE_URL") or (
    "https://test.api.amadeus.com" if ENV in ("sandbox", "test") else "https://api.amadeus.com"
)

# Credenciais
CLIENT_ID = os.getenv("AMADEUS_API_KEY")
CLIENT_SECRET = os.getenv("AMADEUS_API_SECRET")

# Telegram
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# Parâmetros de alerta (os testes mexem nestas variáveis!)
MAX_PRECO_PP = float(os.getenv("MAX_PRECO_PP", "1200"))
MIN_DISCOUNT_PCT = float(os.getenv("MIN_DISCOUNT_PCT", "0.25"))

# Histórico
HISTORY_PATH = Path(os.getenv("HISTORY_PATH", "data/history.csv"))
HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
CSV_HEADERS = ["ts_utc","origem","destino","departure_date","price_total","currency","notified","reason","airline"]

# ===================== FUNÇÕES USADAS NOS TESTES ===
def get_token() -> str:
    """
    Obtém o access_token OAuth2 da Amadeus.
    Usa CLIENT_ID / CLIENT_SECRET e BASE_URL definidos no módulo.
    """
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
        raise

def deve_alertar(preco_atual: float, melhor_anterior: Optional[float]) -> Tuple[bool, str]:
    """
    Regras de alerta:
    1) Se o preço atual <= MAX_PRECO_PP => alerta (motivo '≤ teto ...')
    2) Senão, se houve queda percentual >= MIN_DISCOUNT_PCT em relação ao melhor_anterior => alerta ('queda xx%')
    3) Caso contrário => sem alerta ('sem queda significativa')
    """
    try:
        preco_atual = float(preco_atual)
    except Exception:
        return False, "valor inválido"

    # Regra do teto
    if preco_atual <= MAX_PRECO_PP:
        # ex.: "≤ teto 1200"
        if MAX_PRECO_PP.is_integer():
            return True, f"≤ teto {int(MAX_PRECO_PP)}"
        return True, f"≤ teto {MAX_PRECO_PP}"

    # Regra de queda percentual (se houver referência)
    if melhor_anterior is not None and melhor_anterior not in (float("inf"), 0):
        try:
            melhor_anterior = float(melhor_anterior)
            desconto = (melhor_anterior - preco_atual) / melhor_anterior
            if desconto >= MIN_DISCOUNT_PCT:
                return True, f"queda {desconto:.0%}"
        except Exception:
            pass

    return False, "sem queda significativa"

# ===================== RESTO DO MONITOR =============
def enviar_telegram(text: str):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        log("Telegram não configurado (TOKEN/CHAT_ID ausentes).", "WARNING")
        return
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": text},
            timeout=30,
        )
        r.raise_for_status()
        log("Mensagem enviada ao Telegram.", "SUCCESS")
    except requests.RequestException as e:
        log(f"Erro ao enviar Telegram: {e}", "ERROR")

def append_history_row(row: Dict[str, Any]):
    write_header = not HISTORY_PATH.exists()
    try:
        with HISTORY_PATH.open("a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=CSV_HEADERS)
            if write_header:
                w.writeheader()
            w.writerow(row)
    except OSError as e:
        log(f"Erro ao gravar histórico: {e}", "ERROR")

def buscar_passagens(token: str, origem: str, destino: str, date_str: str) -> Optional[Dict[str, Any]]:
    params = {
        "originLocationCode": origem,
        "destinationLocationCode": destino,
        "departureDate": date_str,
        "adults": "1",
        "currencyCode": Config.CURRENCY,
        "max": str(Config.MAX_OFFERS),
    }
    try:
        r = requests.get(
            f"{BASE_URL}/v2/shopping/flight-offers",
            headers={"Authorization": f"Bearer {token}"},
            params=params,
            timeout=60,
        )
        r.raise_for_status()
        return r.json()
    except requests.RequestException as e:
        log(f"Erro {origem}->{destino} {date_str}: {e}", "ERROR")
        return None

def extract_airline(offer: Dict[str, Any], dictionaries: Dict[str, Any]) -> str:
    """
    Tenta obter o nome da companhia:
      - operating.carrierCode / carrierCode (IATA) -> dictionaries['carriers'][code]
      - se não achar nome, retorna o código IATA
    """
    try:
        seg0 = offer["itineraries"][0]["segments"][0]
        op = seg0.get("operating", {}).get("carrierCode") or seg0.get("operatingCarrierCode") or seg0.get("operatingCarrier")
        mk = seg0.get("carrierCode") or seg0.get("marketingCarrierCode") or seg0.get("marketingCarrier")
        code = op or mk
        if not code:
            return "N/A"
        name = None
        if dictionaries and "carriers" in dictionaries:
            name = dictionaries["carriers"].get(code)
        return name or code
    except Exception:
        return "N/A"

def find_cheapest_offer(payload: Dict[str, Any]) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    if not payload or "data" not in payload or not payload["data"]:
        return None, None
    try:
        cheapest = min(
            payload["data"],
            key=lambda x: float(x.get("price", {}).get("total", float("inf")))
        )
        dictionaries = payload.get("dictionaries", {})
        return cheapest, dictionaries
    except Exception as e:
        log(f"Erro ao escolher mais barata: {e}", "ERROR")
        return None, None

def gerar_datas() -> List[str]:
    today = datetime.utcnow().date()
    out = []
    for _ in range(Config.SAMPLE_DEPARTURES):
        delta = random.randint(Config.DAYS_AHEAD_FROM, Config.DAYS_AHEAD_TO)
        out.append((today + timedelta(days=delta)).strftime("%Y-%m-%d"))
    return out

def resumo_msg(origem: str, destino: str, date_str: str, price: float, currency: str, airline: str, motivo: str) -> str:
    price_fmt = f"{price:.2f}"
    return f"✈️ {origem} → {destino} em {date_str}: {price_fmt} {currency} ({airline}) — {motivo}"

def process_destino(token: str, origem: str, destino: str, melhores: Dict[Tuple[str,str], float]):
    log(f"🔎 {origem} → {destino}")
    best_key = (origem, destino)
    melhor_anterior = melhores.get(best_key, float("inf"))

    for date_str in gerar_datas():
        time.sleep(Config.REQUEST_DELAY)
        payload = buscar_passagens(token, origem, destino, date_str)
        if not payload:
            continue

        cheapest, dictionaries = find_cheapest_offer(payload)
        if not cheapest:
            continue

        price = float(cheapest["price"]["total"])
        currency = cheapest["price"].get("currency", Config.CURRENCY)
        airline = extract_airline(cheapest, dictionaries)

        # data real do voo (fallback para a data pesquisada)
        try:
            date_real = cheapest["itineraries"][0]["segments"][0]["departure"]["at"][:10]
        except Exception:
            date_real = date_str

        alert, motivo = deve_alertar(price, melhor_anterior)
        notified = False
        if alert:
            enviar_telegram(resumo_msg(origem, destino, date_real, price, currency, airline, motivo))
            notified = True

        append_history_row({
            "ts_utc": datetime.utcnow().isoformat()+"Z",
            "origem": origem,
            "destino": destino,
            "departure_date": date_real,
            "price_total": f"{price:.2f}",
            "currency": currency,
            "notified": "1" if notified else "0",
            "reason": motivo,
            "airline": airline,
        })

        if price < melhor_anterior:
            melhor_anterior = price
            melhores[best_key] = price

def main():
    banner_env = "🚀 PRODUÇÃO" if ENV == "production" else "🔧 SANDBOX"
    log(f"Iniciando monitor | ENV={ENV} ({banner_env}) | BASE={BASE_URL}")
    token = get_token()
    melhores: Dict[Tuple[str,str], float] = {}
    for dest in Config.DESTINOS:
        process_destino(token, Config.ORIGEM, dest, melhores)
    log("Monitoramento concluído.", "SUCCESS")

if __name__ == "__main__":
    main()
