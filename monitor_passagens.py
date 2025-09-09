#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import csv
import time
import random
import requests
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

# =========================
# Config / Ambiente
# =========================
def _capitais_brasil_sem(orig: str) -> str:
    caps = [
        "GIG","SDU","GRU","CGH","VCP","BSB","CNF","VIX","SSA","FOR","REC","NAT","MCZ","AJU",
        "MAO","BEL","SLZ","THE","JPA","POA","FLN","CWB","CGR","CGB","PMW","PVH","BVB","RBR","GYN"
    ]
    caps = [c for c in caps if c != orig]
    return ",".join(caps)

ORIGEM_DEFAULT = "GYN"

class Config:
    # Básico
    ORIGEM = os.getenv("ORIGEM", ORIGEM_DEFAULT).strip().upper()
    DESTINOS = [d.strip().upper() for d in os.getenv(
        "DESTINOS", _capitais_brasil_sem(ORIGEM)
    ).split(",") if d.strip()]
    CURRENCY = os.getenv("CURRENCY", "BRL")

    # Datas/varredura
    DAYS_AHEAD_FROM = int(os.getenv("DAYS_AHEAD_FROM", "10"))
    DAYS_AHEAD_TO   = int(os.getenv("DAYS_AHEAD_TO", "90"))
    SAMPLE_DEPARTURES = int(os.getenv("SAMPLE_DEPARTURES", "2"))
    STAY_NIGHTS_MIN   = int(os.getenv("STAY_NIGHTS_MIN", "5"))
    STAY_NIGHTS_MAX   = int(os.getenv("STAY_NIGHTS_MAX", "7"))

    # Amadeus / requests
    MAX_OFFERS = int(os.getenv("MAX_OFFERS", "5"))
    REQUEST_DELAY = float(os.getenv("REQUEST_DELAY", "0.8"))
    REQUEST_TIMEOUT = float(os.getenv("REQUEST_TIMEOUT", "30"))
    MAX_RETRIES = int(os.getenv("MAX_RETRIES", "3"))
    BACKOFF_FACTOR = float(os.getenv("BACKOFF_FACTOR", "0.7"))
    USER_AGENT = os.getenv("USER_AGENT", "FlightMonitor/1.3 (+github-actions)")

    # Alertas
    MAX_PRECO_PP = float(os.getenv("MAX_PRECO_PP", "1200"))  # teto por TRECHO
    MIN_DISCOUNT_PCT = float(os.getenv("MIN_DISCOUNT_PCT", "0.25"))
    ONLY_CAP_BELOW = os.getenv("ONLY_CAP_BELOW", "false").lower() in ("1","true","yes","y")
    LEG_CAP_ENFORCE_BOTH = os.getenv("LEG_CAP_ENFORCE_BOTH", "true").lower() in ("1","true","yes","y")

    # Relatório / histórico
    HISTORY_PATH = Path(os.getenv("HISTORY_PATH", "data/history.csv"))
    HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)

    # Tempo máximo de execução (segundos)
    TIME_BUDGET_SECONDS = int(os.getenv("TIME_BUDGET_SECONDS", "480"))

    # Alternativas na volta
    SHOW_RETURN_ALTS = os.getenv("SHOW_RETURN_ALTS", "true").lower() in ("1","true","yes","y")
    ALT_TOP_N = int(os.getenv("ALT_TOP_N", "3"))
    ALT_MIN_SAVING_BRL = float(os.getenv("ALT_MIN_SAVING_BRL", "0"))

# Secrets / ambiente Amadeus
CLIENT_ID = os.getenv("AMADEUS_API_KEY")
CLIENT_SECRET = os.getenv("AMADEUS_API_SECRET")
ENV = os.getenv("AMADEUS_ENV", "sandbox").strip().lower()
BASE_URL = "https://test.api.amadeus.com" if ENV in ("sandbox","test") else "https://api.amadeus.com"
ENV_LABEL = "sandbox" if ENV in ("sandbox","test") else "production"

# Telegram
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID")
TG_PARSE_MODE      = os.getenv("TG_PARSE_MODE", "HTML")

# Sessão HTTP reusável
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": Config.USER_AGENT})

# =========================
# Utilitários
# =========================
def log(msg: str, level: str = "INFO"):
    icons = {"INFO":"ⓘ","SUCCESS":"✅","ERROR":"❌","WARNING":"⚠️","DEBUG":"🔎"}
    print(f"[{datetime.utcnow().isoformat()}Z] {icons.get(level,' ')} {msg}")

def tg_send(text: str):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        SESSION.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": TG_PARSE_MODE, "disable_web_page_preview": True},
            timeout=20,
        )
    except Exception as e:
        log(f"Falha Telegram: {e}", "WARNING")

CSV_HEADERS = ["ts_utc","origem","destino","departure_date","price_total","currency","notified","reason","airline","leg"]

def append_history(row: Dict):
    write_header = not Config.HISTORY_PATH.exists()
    try:
        with Config.HISTORY_PATH.open("a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=CSV_HEADERS)
            if write_header: w.writeheader()
            w.writerow(row)
    except Exception as e:
        log(f"Erro ao gravar histórico: {e}", "WARNING")

def load_best_totals() -> Dict[Tuple[str,str], float]:
    best = {}
    if not Config.HISTORY_PATH.exists():
        return best
    try:
        with Config.HISTORY_PATH.open("r", encoding="utf-8") as f:
            r = csv.DictReader(f)
            for row in r:
                try:
                    key = (row["origem"], row["destino"])
                    val = float(row["price_total"])
                    if row.get("leg","") == "TOTAL":
                        if key not in best or val < best[key]:
                            best[key] = val
                except Exception:
                    continue
    except Exception:
        pass
    return best

def _safe_float(v, default=float("inf")):
    try:
        return float(v)
    except Exception:
        return default

def _extract_airline_name(offer: Dict) -> str:
    try:
        dct = offer.get("dictionaries", {}) or {}
        carriers = dct.get("carriers", {}) or {}
        seg0 = offer["itineraries"][0]["segments"][0]
        code = seg0.get("marketingCarrier") or seg0.get("carrierCode")
        name = carriers.get(code, "")
        code_str = f" ({code})" if code else ""
        return (f"{name}{code_str}".strip() or "N/A")
    except Exception:
        return "N/A"

def google_flights_deeplink(orig: str, dest: str, ida: str, volta: Optional[str]) -> str:
    base = "https://www.google.com/travel/flights"
    # Formato: #flt=ORIG.DEST.YYYY-MM-DD*DEST.ORIG.YYYY-MM-DD
    if volta:
        return f"{base}?hl=pt-BR&curr=BRL#flt={orig}.{dest}.{ida}*{dest}.{orig}.{volta}"
    return f"{base}?hl=pt-BR&curr=BRL#flt={orig}.{dest}.{ida}"

# =========================
# Amadeus API
# =========================
def get_token() -> str:
    if not CLIENT_ID or not CLIENT_SECRET:
        log("AMADEUS_API_KEY/AMADEUS_API_SECRET ausentes.", "ERROR")
        sys.exit(1)
    for attempt in range(1, Config.MAX_RETRIES + 1):
        try:
            resp = SESSION.post(
                f"{BASE_URL}/v1/security/oauth2/token",
                data={"grant_type":"client_credentials","client_id":CLIENT_ID,"client_secret":CLIENT_SECRET},
                timeout=Config.REQUEST_TIMEOUT,
            )
            if resp.status_code == 200:
                return resp.json().get("access_token")
            log(f"Falha token (tentativa {attempt}): {resp.status_code} {resp.text[:200]}", "WARNING")
        except requests.RequestException as e:
            log(f"Erro token (tentativa {attempt}): {e}", "WARNING")
        time.sleep(Config.BACKOFF_FACTOR * attempt)
    log("Exaustão de tentativas ao obter token.", "ERROR")
    sys.exit(1)

def buscar_one_way(token: str, origem: str, destino: str, data: str) -> Optional[Dict]:
    hdr = {"Authorization": f"Bearer {token}"}
    params = {
        "originLocationCode": origem,
        "destinationLocationCode": destino,
        "departureDate": data,
        "adults": "1",
        "currencyCode": Config.CURRENCY,
        "max": str(Config.MAX_OFFERS),
    }
    for attempt in range(1, Config.MAX_RETRIES + 1):
        try:
            resp = SESSION.get(
                f"{BASE_URL}/v2/shopping/flight-offers",
                headers=hdr, params=params, timeout=Config.REQUEST_TIMEOUT,
            )
            if resp.status_code == 200:
                return resp.json()
            if resp.status_code == 429:
                wait = Config.BACKOFF_FACTOR * attempt + 0.5
                log(f"429 rate-limit. Aguardando {wait:.1f}s…", "WARNING")
                time.sleep(wait); continue
            if 500 <= resp.status_code < 600:
                wait = Config.BACKOFF_FACTOR * attempt
                log(f"{resp.status_code} server error. Retry em {wait:.1f}s…", "WARNING")
                time.sleep(wait); continue
            log(f"Falha busca {origem}->{destino} {data}: {resp.status_code} {resp.text[:180]}", "WARNING")
            return None
        except requests.RequestException as e:
            log(f"Erro rede {origem}->{destino} {data} (tentativa {attempt}): {e}", "WARNING")
            time.sleep(Config.BACKOFF_FACTOR * attempt)
    return None

def _cheapest(offers: Optional[Dict]) -> Optional[Dict]:
    if not offers or not offers.get("data"):
        return None
    try:
        return min(offers["data"], key=lambda x: _safe_float(x.get("price",{}).get("total")))
    except Exception:
        return None

def _top_offers(offers: Optional[Dict], top_n: int) -> List[Tuple[float, str]]:
    out: List[Tuple[float,str]] = []
    if not offers or not offers.get("data"):
        return out
    dictionaries = offers.get("dictionaries", {})
    for off in offers["data"]:
        p = _safe_float(off.get("price",{}).get("total"))
        airline = "N/A"
        try:
            carriers = dictionaries.get("carriers", {}) or {}
            seg0 = off["itineraries"][0]["segments"][0]
            code = seg0.get("marketingCarrier") or seg0.get("carrierCode")
            airline = (carriers.get(code, "") or "N/A") + (f" ({code})" if code else "")
        except Exception:
            pass
        out.append((p, airline.strip()))
    out.sort(key=lambda t: t[0])
    return out[:top_n]

# =========================
# Datas / lógica
# =========================
def _datas_ida() -> List[str]:
    today = datetime.utcnow().date()
    picks = set()
    while len(picks) < Config.SAMPLE_DEPARTURES:
        delta = random.randint(Config.DAYS_AHEAD_FROM, Config.DAYS_AHEAD_TO)
        picks.add((today + timedelta(days=delta)).strftime("%Y-%m-%d"))
    return sorted(picks)

def _datas_volta(ida: str) -> List[str]:
    d = datetime.strptime(ida, "%Y-%m-%d").date()
    return [(d + timedelta(days=n)).strftime("%Y-%m-%d")
            for n in range(Config.STAY_NIGHTS_MIN, Config.STAY_NIGHTS_MAX + 1)]

def deve_alertar(preco_total: float, melhor_anterior_total: Optional[float],
                 leg_prices: Optional[List[float]] = None) -> Tuple[bool, str]:
    """
    - Se ONLY_CAP_BELOW=true:
        * Se LEG_CAP_ENFORCE_BOTH=true: exige TODOS os trechos <= MAX_PRECO_PP.
        * Senão: média por trecho (total/2) <= MAX_PRECO_PP.
      Ignora regra de queda.
    - Caso contrário: mantém lógica antiga (teto OU queda % no total).
    """
    cap = Config.MAX_PRECO_PP

    if Config.ONLY_CAP_BELOW:
        if Config.LEG_CAP_ENFORCE_BOTH and leg_prices:
            ok = all(p <= cap for p in leg_prices)
            return (ok, f"≤ teto {int(cap)} por trecho" if ok else f"acima do teto {int(cap)}")
        else:
            ok = (preco_total / 2.0) <= cap
            return (ok, f"≤ teto médio {int(cap)}" if ok else f"acima do teto {int(cap)}")

    # ---- lógica antiga (compatível com testes) ----
    if preco_total/2.0 <= cap:
        return True, f"≤ teto {int(cap)}"
    if melhor_anterior_total not in (None, float("inf")):
        queda = (melhor_anterior_total - preco_total) / melhor_anterior_total
        if queda >= Config.MIN_DISCOUNT_PCT:
            return True, f"queda {queda:.0%}"
    return False, "sem queda relevante"

def format_roundtrip_msg(orig: str, dest: str, ida: str, preco_ida: float, cia_ida: str,
                         volta: str, preco_volta: float, cia_volta: str,
                         total: float, motivo: str, alts_volta: Optional[List[Tuple[float,str]]]) -> str:
    lines = []
    lines.append(f"✈️ <b>{orig} → {dest}</b>")
    lines.append(f"• <b>Ida</b> {ida}: {preco_ida:.2f} {Config.CURRENCY} ({cia_ida})")
    lines.append(f"• <b>Volta</b> {volta}: {preco_volta:.2f} {Config.CURRENCY} ({cia_volta})")
    lines.append(f"• <b>Total</b>: {total:.2f} {Config.CURRENCY} — {motivo}")
    if Config.SHOW_RETURN_ALTS and alts_volta:
        try:
            alts_fmt = ", ".join([f"{nm.split(' (')[0]} {p:.0f}" for p, nm in alts_volta])
            lines.append(f"↩️ <i>Alternativas (volta)</i>: {alts_fmt}")
        except Exception:
            pass
    return "\n".join(lines)

# =========================
# Processamento por destino
# =========================
def process_destino_roundtrip(token: str, origem: str, destino: str, best_totals: Dict[Tuple[str,str], float]):
    log(f"Processando {origem} → {destino}")
    melhor_global = best_totals.get((origem, destino), float("inf"))

    melhor_combo = None  # (total, ida_date, ida_offer, volta_date, volta_offer, alts_volta)
    for ida in _datas_ida():
        offers_out = buscar_one_way(token, origem, destino, ida)
        ida_offer = _cheapest(offers_out)
        if not ida_offer:
            continue
        preco_out = _safe_float(ida_offer["price"]["total"])
        cia_out  = _extract_airline_name(ida_offer)

        for volta in _datas_volta(ida):
            time.sleep(Config.REQUEST_DELAY)
            offers_back = buscar_one_way(token, destino, origem, volta)
            back_offer = _cheapest(offers_back)
            if not back_offer:
                continue
            preco_back = _safe_float(back_offer["price"]["total"])
            cia_back   = _extract_airline_name(back_offer)
            total = preco_out + preco_back

            # Alternativas (volta)
            alts_volta: List[Tuple[float,str]] = []
            if Config.SHOW_RETURN_ALTS:
                top_candidates = _top_offers(offers_back, Config.ALT_TOP_N + 2)
                chosen_price = preco_back
                for p,nm in top_candidates:
                    if p + 1e-6 <= chosen_price - Config.ALT_MIN_SAVING_BRL:
                        alts_volta.append((p, nm))
                alts_volta = alts_volta[:Config.ALT_TOP_N]

            if (melhor_combo is None) or (total < melhor_combo[0]):
                melhor_combo = (total, ida, ida_offer, volta, back_offer, alts_volta)

    if not melhor_combo:
        log(f"Nada encontrado para {origem} → {destino}.", "WARNING")
        return

    total, ida, ida_offer, volta, back_offer, alts_volta = melhor_combo
    preco_out = _safe_float(ida_offer["price"]["total"])
    preco_back = _safe_float(back_offer["price"]["total"])
    cia_out  = _extract_airline_name(ida_offer)
    cia_back = _extract_airline_name(back_offer)

    alert, motivo = deve_alertar(total, melhor_global, [preco_out, preco_back])

    if not alert:
        # Não envia nada se não passar no filtro
        log(f"Filtrado pelo teto: {origem}->{destino} {ida}/{volta} | {preco_out:.0f}+{preco_back:.0f} > {Config.MAX_PRECO_PP}", "DEBUG")
        return

    # Mensagem
    msg = format_roundtrip_msg(
        orig=origem, dest=destino,
        ida=ida, preco_ida=preco_out, cia_ida=cia_out,
        volta=volta, preco_volta=preco_back, cia_volta=cia_back,
        total=total, motivo=motivo, alts_volta=alts_volta
    )
    tg_send(msg)

    # Link do Google Flights
    link = google_flights_deeplink(origem, destino, ida, volta)
    tg_send("🔎 Ver no Google Flights\n" + link)

    # Histórico
    nowz = datetime.utcnow().isoformat() + "Z"
    for leg, price, cia in (("IDA", preco_out, cia_out), ("VOLTA", preco_back, cia_back)):
        append_history({
            "ts_utc": nowz, "origem": origem, "destino": destino,
            "departure_date": ida if leg=="IDA" else volta,
            "price_total": f"{price:.2f}", "currency": Config.CURRENCY,
            "notified": "1", "reason": leg, "airline": cia, "leg": leg
        })
    append_history({
        "ts_utc": nowz, "origem": origem, "destino": destino,
        "departure_date": f"{ida}|{volta}",
        "price_total": f"{total:.2f}", "currency": Config.CURRENCY,
        "notified": "1", "reason": "TOTAL", "airline": f"{cia_out} + {cia_back}", "leg": "TOTAL"
    })

# =========================
# Main
# =========================
def main():
    start = time.perf_counter()
    log(f"Iniciando monitor | ENV={ENV_LABEL} | BASE={BASE_URL}")
    token = get_token()
    log("Token OK.", "SUCCESS")

    best_totals = load_best_totals()

    for destino in Config.DESTINOS:
        if time.perf_counter() - start > Config.TIME_BUDGET_SECONDS:
            log(f"⏱️ Tempo limite atingido ({Config.TIME_BUDGET_SECONDS}s). Encerrando cedo.", "WARNING")
            break
        process_destino_roundtrip(token, Config.ORIGEM, destino, best_totals)
        time.sleep(Config.REQUEST_DELAY)

    log("Fim do monitoramento.", "SUCCESS")

if __name__ == "__main__":
    main()