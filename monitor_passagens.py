#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Monitoramento de Passagens (Amadeus + Telegram)

• IDA + VOLTA: escolhe a ida mais barata (origem→destino) e a volta mais barata
  (destino→origem) para a data de retorno. Companhias podem ser diferentes.
• Cache de token com renovação automática.
• Retries com backoff exponencial para chamadas HTTP (429/5xx).
• Logs estruturados (LOG_LEVEL).

Requer segredos no GitHub Actions:
- AMADEUS_API_KEY
- AMADEUS_API_SECRET
- TELEGRAM_BOT_TOKEN
- TELEGRAM_CHAT_ID
"""

from __future__ import annotations
import os
import sys
import json
import csv
import time
import random
import logging
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, Tuple, List

import requests


# =========================
# Logging
# =========================
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)sZ [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
# Força UTC no formatter visual
logging.Formatter.converter = time.gmtime
log = logging.getLogger("monitor")


# =========================
# Config via variáveis
# =========================
class Config:
    ORIGEM = os.getenv("ORIGEM", "GYN").strip().upper()

    # capitais brasileiras (sem repetir a origem)
    _capitais = "GIG,SDU,SSA,FOR,REC,NAT,MCZ,AJU,MAO,BEL,SLZ,THE,BSB,FLN,POA,CWB,CGR,CGB,CNF,VIX,JPA,PMW,PVH,BVB,RBR,GYN,GRU,CGH"
    DESTINOS = [d for d in dict.fromkeys(os.getenv("DESTINOS", _capitais).split(",")) if d and d.strip().upper() != ORIGEM]

    CURRENCY = os.getenv("CURRENCY", "BRL").strip().upper()

    # janelas / amostragem
    DAYS_AHEAD_FROM = int(os.getenv("DAYS_AHEAD_FROM", "10"))
    DAYS_AHEAD_TO   = int(os.getenv("DAYS_AHEAD_TO",   "90"))
    SAMPLE_DEPARTURES = int(os.getenv("SAMPLE_DEPARTURES", "2"))  # quantas datas de IDA por destino

    # ida + volta
    ROUND_TRIP = os.getenv("ROUND_TRIP", "1").strip() == "1"
    STAY_NIGHTS_MIN = int(os.getenv("STAY_NIGHTS_MIN", "5"))
    STAY_NIGHTS_MAX = int(os.getenv("STAY_NIGHTS_MAX", "10"))
    SAMPLE_STAYS = int(os.getenv("SAMPLE_STAYS", "0"))  # 0 = todas as noites; >0 = amostra aleatória

    # limites / API
    MAX_OFFERS = int(os.getenv("MAX_OFFERS", "5"))
    REQUEST_DELAY = float(os.getenv("REQUEST_DELAY", "1.2"))  # segundos entre chamadas

# Ambiente Amadeus
CLIENT_ID = os.getenv("AMADEUS_API_KEY")
CLIENT_SECRET = os.getenv("AMADEUS_API_SECRET")
ENV = os.getenv("AMADEUS_ENV", "sandbox").strip().lower()
BASE_URL = "https://test.api.amadeus.com" if ENV in ("sandbox", "test") else "https://api.amadeus.com"

# Telegram
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# Regras de alerta (sobre o TOTAL ida+volta)
MAX_PRECO_PP = float(os.getenv("MAX_PRECO_PP", "1200"))
MIN_DISCOUNT_PCT = float(os.getenv("MIN_DISCOUNT_PCT", "0.25"))

# Histórico
DATA_DIR = Path("data")
DATA_DIR.mkdir(parents=True, exist_ok=True)
HISTORY_PATH = Path(os.getenv("HISTORY_PATH", str(DATA_DIR / "history.csv")))
TOKEN_CACHE_PATH = Path(os.getenv("TOKEN_CACHE_PATH", str(DATA_DIR / "amadeus_token.json")))

CSV_HEADERS = [
    "ts_utc", "origem", "destino",
    "departure_date", "return_date",
    "price_ida", "airline_ida",
    "price_volta", "airline_volta",
    "total_price", "currency",
    "notified", "reason"
]


# =========================
# Telegram
# =========================
def enviar_telegram(texto: str):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        log.warning("Telegram não configurado. Pulando envio.")
        return
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": texto},
            timeout=20,
        )
        r.raise_for_status()
        log.info("Mensagem enviada ao Telegram.")
    except Exception as e:
        log.error(f"Erro ao enviar Telegram: {e}")


# =========================
# Histórico (CSV)
# =========================
def load_best_totals() -> Dict[Tuple[str, str], float]:
    best: Dict[Tuple[str, str], float] = {}
    if not HISTORY_PATH.exists():
        return best
    try:
        with HISTORY_PATH.open("r", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                try:
                    key = (row["origem"], row["destino"])
                    total = float(row.get("total_price") or row.get("price_total") or "inf")
                    if total != float("inf"):
                        if key not in best or total < best[key]:
                            best[key] = total
                except Exception:
                    continue
    except Exception as e:
        log.warning(f"Erro lendo histórico: {e}")
    return best


def append_history(row: Dict[str, Any]):
    write_header = not HISTORY_PATH.exists()
    try:
        with HISTORY_PATH.open("a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=CSV_HEADERS)
            if write_header:
                w.writeheader()
            w.writerow(row)
    except Exception as e:
        log.error(f"Erro gravando histórico: {e}")


# =========================
# Token cache
# =========================
def _load_cached_token() -> Optional[str]:
    try:
        if TOKEN_CACHE_PATH.exists():
            data = json.loads(TOKEN_CACHE_PATH.read_text(encoding="utf-8"))
            token = data.get("access_token")
            exp = data.get("expires_at")  # epoch (UTC)
            if token and exp and time.time() < exp - 60:  # margem de 60s
                return token
    except Exception:
        pass
    return None


def _save_token(token: str, expires_in: int):
    try:
        payload = {"access_token": token, "expires_at": time.time() + int(expires_in)}
        TOKEN_CACHE_PATH.write_text(json.dumps(payload), encoding="utf-8")
    except Exception as e:
        log.warning(f"Não foi possível salvar token em cache: {e}")


def get_token() -> str:
    """
    Mantém interface usada nos testes.
    Retorna token do cache se válido; senão, solicita um novo.
    """
    cached = _load_cached_token()
    if cached:
        return cached

    if not CLIENT_ID or not CLIENT_SECRET:
        log.error("AMADEUS_API_KEY/AMADEUS_API_SECRET ausentes.")
        sys.exit(1)

    try:
        resp = requests.post(
            f"{BASE_URL}/v1/security/oauth2/token",
            data={"grant_type": "client_credentials",
                  "client_id": CLIENT_ID,
                  "client_secret": CLIENT_SECRET},
            timeout=30,
            headers={"User-Agent": "apgyn/1.1 (+monitor)"},
        )
        resp.raise_for_status()
        js = resp.json()
        token = js["access_token"]
        # Amadeus retorna expires_in (segundos)
        _save_token(token, int(js.get("expires_in", 1800)))
        return token
    except requests.RequestException as e:
        log.error(f"Falha ao obter token: {e}")
        sys.exit(1)


def _refresh_token() -> str:
    # invalida cache e força novo token
    try:
        if TOKEN_CACHE_PATH.exists():
            TOKEN_CACHE_PATH.unlink()
    except Exception:
        pass
    return get_token()


# =========================
# HTTP com retry/backoff
# =========================
RETRY_STATUS = {429, 500, 502, 503, 504}

def _sleep_backoff(attempt: int):
    base = 1.0 * (2 ** attempt)  # 1,2,4,8...
    jitter = random.uniform(0, 0.4)
    time.sleep(base + jitter)

def request_with_retry(method: str, url: str, max_tries: int = 4, **kw) -> requests.Response:
    """
    Tenta até max_tries para status/transientes definidos.
    Não faz refresh de token aqui; isso é tratado pelo caller (buscar_one_way).
    """
    for attempt in range(max_tries):
        try:
            resp = requests.request(method, url, timeout=60, **kw)
            if resp.status_code in RETRY_STATUS:
                log.warning(f"HTTP {resp.status_code} em {url}; tentativa {attempt+1}/{max_tries}")
                if attempt < max_tries - 1:
                    _sleep_backoff(attempt)
                    continue
            return resp
        except requests.RequestException as e:
            log.warning(f"Falha de rede em {url}: {e}; tentativa {attempt+1}/{max_tries}")
            if attempt < max_tries - 1:
                _sleep_backoff(attempt)
                continue
            raise
    return resp  # tipo: ignore


# =========================
# Amadeus: helpers
# =========================
def _extract_airline_name(offer: Dict[str, Any], dictionaries: Optional[Dict[str, Any]]) -> str:
    try:
        seg = offer["itineraries"][0]["segments"][0]
        code = seg.get("carrierCode") or seg.get("marketingCarrier")
        if dictionaries and "carriers" in dictionaries and code:
            return dictionaries["carriers"].get(code) or code
        return code or "N/A"
    except Exception:
        return "N/A"

def _cheapest(offers: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not offers or not offers.get("data"):
        return None
    try:
        return min(offers["data"], key=lambda x: float(x["price"]["total"]))
    except Exception:
        return None


def buscar_one_way(token: str, origem: str, destino: str, date: str) -> Optional[Tuple[float, str, str]]:
    """
    Busca menor preço ONE-WAY para (origem -> destino) em 'date'.
    Retorna (preco, moeda, companhia).
    Faz refresh de token automático em caso de 401.
    """
    url = f"{BASE_URL}/v2/shopping/flight-offers"
    params = {
        "originLocationCode": origem,
        "destinationLocationCode": destino,
        "departureDate": date,
        "adults": "1",
        "currencyCode": Config.CURRENCY,
        "max": str(Config.MAX_OFFERS),
    }
    headers = {
        "Authorization": f"Bearer {token}",
        "User-Agent": "apgyn/1.1 (+monitor)",
        "Accept": "application/json",
    }

    try:
        resp = request_with_retry("GET", url, params=params, headers=headers)
        if resp.status_code == 401:
            # token expirou: renova e tenta 1x
            log.info("Token expirado. Renovando…")
            token2 = _refresh_token()
            headers["Authorization"] = f"Bearer {token2}"
            resp = request_with_retry("GET", url, params=params, headers=headers)

        if resp.status_code >= 400:
            log.warning(f"({origem}→{destino} {date}) HTTP {resp.status_code} {resp.text[:300]}")
            return None

        data = resp.json()
        cheapest = _cheapest(data)
        if not cheapest:
            return None

        price = float(cheapest["price"]["total"])
        currency = cheapest["price"]["currency"]
        airline = _extract_airline_name(cheapest, data.get("dictionaries"))
        return price, currency, airline

    except requests.RequestException as e:
        log.error(f"Erro na busca {origem}→{destino} {date}: {e}")
        return None


# =========================
# Regras de alerta
# =========================
def deve_alertar(preco_atual: float, melhor_anterior: Optional[float]) -> Tuple[bool, str]:
    # Regra 1: total ≤ teto
    if preco_atual <= MAX_PRECO_PP:
        return True, f"≤ teto {int(MAX_PRECO_PP)}"
    # Regra 2: queda percentual vs histórico
    if melhor_anterior and melhor_anterior != float("inf"):
        desc = (melhor_anterior - preco_atual) / melhor_anterior
        if desc >= MIN_DISCOUNT_PCT:
            return True, f"queda {desc:.0%}"
    return False, "sem queda relevante"


# =========================
# Datas
# =========================
def gerar_datas_ida(n: int) -> List[str]:
    hoje = datetime.utcnow().date()
    datas: set[str] = set()
    while len(datas) < n:
        delta = random.randint(Config.DAYS_AHEAD_FROM, Config.DAYS_AHEAD_TO)
        datas.add((hoje + timedelta(days=delta)).strftime("%Y-%m-%d"))
    return sorted(datas)

def noites_amostradas() -> List[int]:
    """Retorna todas as noites no intervalo ou uma amostra aleatória se SAMPLE_STAYS>0."""
    nights = list(range(Config.STAY_NIGHTS_MIN, Config.STAY_NIGHTS_MAX + 1))
    if Config.SAMPLE_STAYS and Config.SAMPLE_STAYS < len(nights):
        return sorted(random.sample(nights, Config.SAMPLE_STAYS))
    return nights

def datas_retorno(dep_date: str) -> List[str]:
    base = datetime.strptime(dep_date, "%Y-%m-%d").date()
    return [(base + timedelta(days=n)).strftime("%Y-%m-%d") for n in noites_amostradas()]


# =========================
# Processamento
# =========================
def process_destino_roundtrip(token: str, origem: str, destino: str, best_map: Dict[Tuple[str, str], float]):
    log.info(f"🔎 {origem} → {destino} (ida+volta)")
    candidatos: List[Tuple[float, str, str, float, str, float, str, str]] = []

    for dep in gerar_datas_ida(Config.SAMPLE_DEPARTURES):
        ida = buscar_one_way(token, origem, destino, dep)
        time.sleep(Config.REQUEST_DELAY)
        if not ida:
            continue
        ida_price, curr, ida_air = ida

        for ret in datas_retorno(dep):
            volta = buscar_one_way(token, destino, origem, ret)
            time.sleep(Config.REQUEST_DELAY)
            if not volta:
                continue
            volta_price, curr2, volta_air = volta
            total = ida_price + volta_price
            candidatos.append((total, dep, ret, ida_price, ida_air, volta_price, volta_air, curr))

    if not candidatos:
        msg = f"❌ Sem ofertas para {origem} ↔ {destino} nas datas testadas."
        log.info(msg)
        enviar_telegram(msg)
        return

    total, dep, ret, p_ida, air_ida, p_volta, air_volta, curr = min(candidatos, key=lambda x: x[0])

    key = (origem, destino)
    melhor_hist = best_map.get(key, float("inf"))
    ok, motivo = deve_alertar(total, melhor_hist)

    texto = (
        f"✈️ {origem} ↔ {destino}\n"
        f"• Ida {dep}: {p_ida:.2f} {curr} ({air_ida or 'N/A'})\n"
        f"• Volta {ret}: {p_volta:.2f} {curr} ({air_volta or 'N/A'})\n"
        f"• Total: {total:.2f} {curr} — {motivo}"
    )

    if ok:
        enviar_telegram(texto)
    else:
        log.info(texto)

    append_history({
        "ts_utc": datetime.utcnow().isoformat() + "Z",
        "origem": origem,
        "destino": destino,
        "departure_date": dep,
        "return_date": ret,
        "price_ida": f"{p_ida:.2f}",
        "airline_ida": air_ida or "",
        "price_volta": f"{p_volta:.2f}",
        "airline_volta": air_volta or "",
        "total_price": f"{total:.2f}",
        "currency": curr,
        "notified": "1" if ok else "0",
        "reason": motivo,
    })

    if total < melhor_hist:
        best_map[key] = total


def main():
    log.info(f"Iniciando monitor | ENV={ENV} | BASE={BASE_URL}")
    token = get_token()
    best_map = load_best_totals()

    if Config.ROUND_TRIP:
        for dest in Config.DESTINOS:
            process_destino_roundtrip(token, Config.ORIGEM, dest, best_map)
    else:
        log.warning("ROUND_TRIP=0 não suportado nesta versão (use ida+volta).")

    log.info("Execução finalizada.")


if __name__ == "__main__":
    main()