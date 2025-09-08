#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Monitoramento de Passagens Aéreas (Amadeus + Telegram)

- Origem fixa: GYN
- Destinos: capitais do Brasil (pode sobrescrever via DESTINOS)
- Busca ida+volta (ida e volta podem ser de companhias diferentes)
- Envia alertas no Telegram e registra histórico CSV
- Melhorias: sessão HTTP com Retry/Backoff, validação robusta, User-Agent,
  concorrência opcional para buscas de volta, REQUEST_TIMEOUT e LOG_LEVEL.
"""

from __future__ import annotations

import os
import sys
import csv
import time
import random
from typing import Optional, Tuple, Dict, Any, List
from datetime import datetime, timedelta
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# ============== Log ==============
_LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").strip().upper()
_LEVEL_ORDER = {"DEBUG": 10, "INFO": 20, "SUCCESS": 21, "WARNING": 30, "ERROR": 40}
_THRESHOLD = _LEVEL_ORDER.get(_LOG_LEVEL, 20)

def log(msg: str, level: str = "INFO") -> None:
    lvl = _LEVEL_ORDER.get(level.upper(), 20)
    if lvl < _THRESHOLD:
        return
    icons = {"INFO": "ⓘ", "SUCCESS": "✅", "ERROR": "❌", "WARNING": "⚠️", "DEBUG": "🔎"}
    print(f"[{datetime.utcnow().isoformat()}Z] {icons.get(level, ' ')} {msg}")


# ============== Config ==============
_CAPITAIS_DEFAULT = (
    "GIG,SDU,GRU,CGH,BSB,CNF,VIX,CWB,FLN,POA,GYN,CGR,CGB,PMW,"
    "RBR,PVH,BVB,MAO,BEL,MCP,SLZ,THE,FOR,NAT,JPA,REC,MCZ,AJU,SSA"
)

def _compute_destinos_from_env(origem: str) -> List[str]:
    raw = os.getenv("DESTINOS", _CAPITAIS_DEFAULT)
    codes = [c.strip().upper() for c in raw.split(",") if c.strip()]
    seen: set[str] = set()
    out: List[str] = []
    for c in codes:
        if c == origem:
            continue
        if c not in seen:
            seen.add(c)
            out.append(c)
    return out

ORIGEM = os.getenv("ORIGEM", "GYN").strip().upper()
DESTINOS = _compute_destinos_from_env(ORIGEM)
CURRENCY = os.getenv("CURRENCY", "BRL")

DAYS_AHEAD_FROM   = int(os.getenv("DAYS_AHEAD_FROM", "10"))
DAYS_AHEAD_TO     = int(os.getenv("DAYS_AHEAD_TO", "90"))
SAMPLE_DEPARTURES = int(os.getenv("SAMPLE_DEPARTURES", "2"))
STAY_NIGHTS_MIN   = int(os.getenv("STAY_NIGHTS_MIN", "5"))
STAY_NIGHTS_MAX   = int(os.getenv("STAY_NIGHTS_MAX", "10"))

MAX_OFFERS      = int(os.getenv("MAX_OFFERS", "5"))
REQUEST_DELAY   = float(os.getenv("REQUEST_DELAY", "1.2"))
REQUEST_TIMEOUT = float(os.getenv("REQUEST_TIMEOUT", "30"))

# Regras (testes mexem nessas variáveis em runtime)
MAX_PRECO_PP     = float(os.getenv("MAX_PRECO_PP", "1200"))
MIN_DISCOUNT_PCT = float(os.getenv("MIN_DISCOUNT_PCT", "0.25"))

CLIENT_ID     = os.getenv("AMADEUS_API_KEY")
CLIENT_SECRET = os.getenv("AMADEUS_API_SECRET")

ENV = os.getenv("AMADEUS_ENV", "sandbox").strip().lower()
BASE_URL = os.getenv("AMADEUS_BASE_URL") or (
    "https://test.api.amadeus.com" if ENV in ("sandbox", "test") else "https://api.amadeus.com"
)

TELEGRAM_TOKEN   = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
TG_PARSE_MODE    = os.getenv("TG_PARSE_MODE", "HTML")

HISTORY_PATH = Path(os.getenv("HISTORY_PATH", "data/history.csv"))
HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)

CSV_HEADERS = [
    "ts_utc","origem","destino","departure_date","return_date",
    "price_total","currency","price_outbound","price_inbound",
    "airline_outbound","airline_inbound","notified","reason","score"
]

AIRLINE_CODE_TO_NAME = {
    "LA": "LATAM Airlines",
    "G3": "GOL Linhas Aéreas",
    "AD": "Azul Linhas Aéreas",
    "VO": "VOEPASS",
    "2Z": "Azul Conecta",
}

# HTTP/Retry tuning
MAX_RETRIES     = int(os.getenv("MAX_RETRIES", "3"))
BACKOFF_FACTOR  = float(os.getenv("BACKOFF_FACTOR", "0.6"))
RETRY_STATUSES  = (429, 500, 502, 503, 504)
USER_AGENT      = os.getenv("USER_AGENT", "FlightMonitor/1.0 (+github actions)")

# Concorrência opcional (apenas nas buscas de volta)
CONCURRENCY = max(1, int(os.getenv("CONCURRENCY", "1")))  # 1 = desativado

# ============== HTTP Session com Retry ==============
def _build_session() -> requests.Session:
    sess = requests.Session()
    retry = Retry(
        total=MAX_RETRIES,
        read=MAX_RETRIES,
        connect=MAX_RETRIES,
        backoff_factor=BACKOFF_FACTOR,
        status_forcelist=RETRY_STATUSES,
        allowed_methods=frozenset(["GET", "POST"]),
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=20, pool_maxsize=20)
    sess.mount("https://", adapter)
    sess.mount("http://", adapter)
    sess.headers.update({"User-Agent": USER_AGENT})
    return sess

SESSION = _build_session()

# ============== Util ==============
def _safe_float(value: Any, default: float = float("inf")) -> float:
    try:
        return float(value)
    except Exception:
        return default

# ============== Funções exigidas pelos testes ==============
def get_token() -> str:
    """Usa requests.post (não SESSION) para permitir o monkeypatch dos testes."""
    if not CLIENT_ID or not CLIENT_SECRET:
        log("AMADEUS_API_KEY/AMADEUS_API_SECRET ausentes.", "ERROR")
        sys.exit(1)
    try:
        resp = requests.post(  # ← importante para os testes
            f"{BASE_URL}/v1/security/oauth2/token",
            data={
                "grant_type": "client_credentials",
                "client_id": CLIENT_ID,
                "client_secret": CLIENT_SECRET,
            },
            timeout=REQUEST_TIMEOUT,
            headers={"User-Agent": USER_AGENT},
        )
        if resp.status_code != 200:
            log(f"Falha ao obter token: {resp.status_code} {resp.text[:200]}", "ERROR")
            sys.exit(1)
        return resp.json()["access_token"]
    except requests.RequestException as e:
        log(f"Falha ao obter token: {e}", "ERROR")
        sys.exit(1)

def deve_alertar(preco_atual: float, melhor_anterior: Optional[float]) -> Tuple[bool, str]:
    try:
        teto = float(MAX_PRECO_PP)
    except Exception:
        teto = float("inf")
    if preco_atual <= teto:
        teto_txt = f"{int(teto)}" if abs(teto - int(teto)) < 1e-9 else f"{teto:.2f}"
        return True, f"≤ teto {teto_txt}"

    try:
        min_drop = float(MIN_DISCOUNT_PCT)
    except Exception:
        min_drop = 0.0
    if melhor_anterior is not None and melhor_anterior > 0 and preco_atual < melhor_anterior:
        queda = (melhor_anterior - preco_atual) / melhor_anterior
        if queda >= min_drop:
            return True, f"queda {queda:.0%}"
    return False, "sem queda relevante"

# ============== Amadeus helpers ==============
def _extract_airline_name(offer: Dict[str, Any]) -> str:
    try:
        seg0 = offer["itineraries"][0]["segments"][0]
    except Exception:
        return "N/A"
    code = None
    if isinstance(seg0.get("operating"), dict):
        code = seg0["operating"].get("carrierCode")
    code = code or seg0.get("carrierCode") or seg0.get("marketingCarrierCode")
    name = AIRLINE_CODE_TO_NAME.get(code, None) if code else None
    name = name or seg0.get("operatingCarrierName") or seg0.get("marketingCarrierName")
    if code and name: return f"{name} ({code})"
    if code: return code
    return name or "N/A"

def _cheapest(offers_json: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not offers_json or "data" not in offers_json or not offers_json["data"]:
        return None
    try:
        cheapest = min(
            offers_json["data"],
            key=lambda x: _safe_float(x.get("price", {}).get("total"))
        )
    except Exception:
        return None
    cheapest = dict(cheapest)
    cheapest["airline"] = _extract_airline_name(cheapest)
    return cheapest

def buscar_one_way(token: str, origem: str, destino: str, date_yyyy_mm_dd: str) -> Optional[Dict[str, Any]]:
    params = {
        "originLocationCode": origem,
        "destinationLocationCode": destino,
        "departureDate": date_yyyy_mm_dd,
        "adults": "1",
        "currencyCode": CURRENCY,
        "max": str(MAX_OFFERS),
    }
    headers = {"Authorization": f"Bearer {token}"}
    try:
        r = SESSION.get(
            f"{BASE_URL}/v2/shopping/flight-offers",
            headers=headers, params=params, timeout=REQUEST_TIMEOUT,
        )
        if r.status_code == 429:
            log("429 recebido. Aumentando delay temporariamente.", "WARNING")
            time.sleep(max(3.0, REQUEST_DELAY * 2))
        if r.status_code != 200:
            log(f"Amadeus {origem}->{destino} {date_yyyy_mm_dd} HTTP {r.status_code}: {r.text[:300]}", "WARNING")
            return None
        return r.json()
    except requests.RequestException as e:
        log(f"Erro HTTP {origem}->{destino} {date_yyyy_mm_dd}: {e}", "ERROR")
        return None

# ============== Telegram ==============
def tg_send(text: str, preview: bool = False) -> None:
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        log("Telegram não configurado. Pulando envio.", "WARNING")
        return
    try:
        r = SESSION.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": text,
                "parse_mode": TG_PARSE_MODE,
                "disable_web_page_preview": (not preview),
            },
            timeout=REQUEST_TIMEOUT,
        )
        if r.status_code != 200:
            log(f"Telegram HTTP {r.status_code}: {r.text[:200]}", "ERROR")
        else:
            log("Mensagem enviada ao Telegram.", "SUCCESS")
    except requests.RequestException as e:
        log(f"Erro ao enviar Telegram: {e}", "ERROR")

# ============== Histórico CSV ==============
def _append_history_row(row: Dict[str, Any]) -> None:
    write_header = not HISTORY_PATH.exists()
    try:
        with HISTORY_PATH.open("a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=CSV_HEADERS)
            if write_header:
                w.writeheader()
            w.writerow(row)
    except Exception as e:
        log(f"Erro ao gravar histórico: {e}", "ERROR")

def _load_best_totals() -> Dict[Tuple[str, str], float]:
    best: Dict[Tuple[str, str], float] = {}
    if not HISTORY_PATH.exists():
        return best
    try:
        with HISTORY_PATH.open("r", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                try:
                    key = (row["origem"], row["destino"])
                    price = float(row["price_total"])
                    if key not in best or price < best[key]:
                        best[key] = price
                except Exception:
                    continue
    except Exception as e:
        log(f"Erro lendo histórico: {e}", "WARNING")
    return best

# ============== Datas ==============
def _datas_ida() -> List[str]:
    hoje = datetime.utcnow().date()
    out: List[str] = []
    for _ in range(max(1, SAMPLE_DEPARTURES)):
        delta = random.randint(DAYS_AHEAD_FROM, DAYS_AHEAD_TO)
        out.append((hoje + timedelta(days=delta)).strftime("%Y-%m-%d"))
    # dedup mantendo ordem
    seen = set(); dedup = []
    for d in out:
        if d not in seen:
            seen.add(d); dedup.append(d)
    return dedup

def _datas_retorno_para(ida: str) -> List[str]:
    d0 = datetime.strptime(ida, "%Y-%m-%d").date()
    ret: List[str] = []
    a, b = sorted((STAY_NIGHTS_MIN, STAY_NIGHTS_MAX))
    for n in range(a, b + 1):
        ret.append((d0 + timedelta(days=n)).strftime("%Y-%m-%d"))
    return ret

# ============== Google Flights deeplink ==============
def google_flights_deeplink(orig: str, dest: str, ida: str, volta: str) -> str:
    return f"https://www.google.com/travel/flights#flt={orig}.{dest}.{ida}*{dest}.{orig}.{volta};c:{CURRENCY};e:1;sd:1;t:e"

# ============== Score (placeholder) ==============
def _score(preco_total: float, preco_out: float, preco_in: float) -> float:
    return 0.0

# ============== Formatação ==============
def _format_msg(origem: str, destino: str, d_ida: str, d_volta: str,
                preco_total: float, moeda: str,
                out_price: float, in_price: float,
                out_airline: str, in_airline: str,
                motivo: str, score: float) -> str:
    return (
        f"✈️ <b>{origem} → {destino}</b>\n"
        f"• Ida {d_ida}: <b>{out_price:.2f} {moeda}</b> ({out_airline})\n"
        f"• Volta {d_volta}: <b>{in_price:.2f} {moeda}</b> ({in_airline})\n"
        f"• <b>Total:</b> {preco_total:.2f} {moeda} — {motivo}"
    )

# ============== Processamento (ida+volta) ==============
def _cheapest_for(token: str, o: str, d: str, date: str) -> Optional[Dict[str, Any]]:
    js = buscar_one_way(token, o, d, date)
    return _cheapest(js)

def process_destino_roundtrip(token: str, origem: str, destino: str, best_totals: Dict[Tuple[str, str], float]) -> None:
    log(f"🔍 {origem} → {destino} (ida+volta)")

    melhor_global = best_totals.get((origem, destino), float("inf"))
    melhor_combo = None  # (total, moeda, d_ida, d_volta, preco_out, preco_in, out_air, in_air)

    for d_ida in _datas_ida():
        time.sleep(REQUEST_DELAY)
        cheapest_out = _cheapest_for(token, origem, destino, d_ida)
        if not cheapest_out:
            continue

        preco_out = _safe_float(cheapest_out.get("price", {}).get("total"))
        moeda     = cheapest_out.get("price", {}).get("currency", CURRENCY)
        out_air   = cheapest_out.get("airline", "N/A")

        # Retornos – opção de concorrência limitada
        retornos = _datas_retorno_para(d_ida)
        results = []

        if CONCURRENCY > 1 and len(retornos) > 1:
            with ThreadPoolExecutor(max_workers=CONCURRENCY) as ex:
                futs = {ex.submit(_cheapest_for, token, destino, origem, d): d for d in retornos}
                for fut in as_completed(futs):
                    d_volta = futs[fut]
                    cheapest_in = fut.result()
                    results.append((d_volta, cheapest_in))
                    time.sleep(REQUEST_DELAY)  # respeita um delay básico
        else:
            for d_volta in retornos:
                time.sleep(REQUEST_DELAY)
                cheapest_in = _cheapest_for(token, destino, origem, d_volta)
                results.append((d_volta, cheapest_in))

        for d_volta, cheapest_in in results:
            if not cheapest_in:
                continue
            preco_in = _safe_float(cheapest_in.get("price", {}).get("total"))
            in_air   = cheapest_in.get("airline", "N/A")
            total    = preco_out + preco_in

            if (melhor_combo is None) or (total < melhor_combo[0]):
                melhor_combo = (total, moeda, d_ida, d_volta, preco_out, preco_in, out_air, in_air)

    if not melhor_combo:
        log(f"Nenhuma combinação encontrada para {origem} → {destino}.", "WARNING")
        _append_history_row({
            "ts_utc": datetime.utcnow().isoformat() + "Z",
            "origem": origem, "destino": destino,
            "departure_date": "", "return_date": "",
            "price_total": "", "currency": CURRENCY,
            "price_outbound": "", "price_inbound": "",
            "airline_outbound": "", "airline_inbound": "",
            "notified": "0", "reason": "sem ofertas", "score": "0",
        })
        return

    total, moeda, d_ida, d_volta, preco_out, preco_in, out_air, in_air = melhor_combo
    alert, motivo = deve_alertar(total, melhor_global)
    notified = False
    score = _score(total, preco_out, preco_in)

    if alert:
        msg = _format_msg(
            origem, destino, d_ida, d_volta, total, moeda,
            preco_out, preco_in, out_air, in_air, motivo, score
        )
        tg_send(msg)  # sem preview
        link = google_flights_deeplink(origem, destino, d_ida, d_volta)
        tg_send(f"🔎 Ver no Google Flights\n{link}", preview=True)
        notified = True
        log(f"Link enviado: {link}", "DEBUG")

    _append_history_row({
        "ts_utc": datetime.utcnow().isoformat() + "Z",
        "origem": origem, "destino": destino,
        "departure_date": d_ida, "return_date": d_volta,
        "price_total": f"{total:.2f}", "currency": moeda,
        "price_outbound": f"{preco_out:.2f}", "price_inbound": f"{preco_in:.2f}",
        "airline_outbound": out_air, "airline_inbound": in_air,
        "notified": "1" if notified else "0",
        "reason": motivo, "score": "0",
    })

    if total < melhor_global:
        best_totals[(origem, destino)] = total

# ============== Main ==============
def main() -> None:
    log(f"Monitor pronto ({'produção' if ENV=='production' else 'sandbox'}). BASE={BASE_URL}")
    token = get_token()
    best_totals = _load_best_totals()
    for destino in DESTINOS:
        try:
            process_destino_roundtrip(token, ORIGEM, destino, best_totals)
        except Exception as e:
            log(f"Erro ao processar {ORIGEM}->{destino}: {e}", "ERROR")
    log("Execução concluída.", "SUCCESS")

if __name__ == "__main__":
    main()