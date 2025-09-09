#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import os
import sys
import csv
import time
import math
import random
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, Tuple, Optional, List

import requests

# ========= Helpers =========

def _capitais_padrao() -> str:
    return (
        "GIG,SDU,SSA,FOR,REC,NAT,MCZ,AJU,MAO,BEL,SLZ,THE,BSB,FLN,POA,CWB,"
        "CGR,CGB,CNF,VIX,JPA,PMW,PVH,BVB,RBR,GRU,CGH"
    )

def _build_destinos(origem: str) -> List[str]:
    capital_str = os.getenv("DESTINOS", _capitais_padrao())
    lst = [d.strip().upper() for d in capital_str.split(",") if d.strip()]
    lst = list(dict.fromkeys(lst))
    return [d for d in lst if d != origem]

def _safe_float(v, default=math.inf) -> float:
    try:
        return float(v)
    except Exception:
        return default

# ========= Config =========

class Config:
    ORIGEM = (os.getenv("ORIGEM", "GYN") or "GYN").strip().upper()
    DESTINOS: List[str] = []

    CURRENCY = os.getenv("CURRENCY", "BRL")

    DAYS_AHEAD_FROM = int(os.getenv("DAYS_AHEAD_FROM", "10"))
    DAYS_AHEAD_TO   = int(os.getenv("DAYS_AHEAD_TO",   "90"))
    SAMPLE_DEPARTURES = int(os.getenv("SAMPLE_DEPARTURES", "2"))

    STAY_NIGHTS_MIN = int(os.getenv("STAY_NIGHTS_MIN", "5"))
    STAY_NIGHTS_MAX = int(os.getenv("STAY_NIGHTS_MAX", "7"))

    MAX_OFFERS = int(os.getenv("MAX_OFFERS", "5"))
    REQUEST_DELAY = float(os.getenv("REQUEST_DELAY", "0.8"))
    REQUEST_TIMEOUT = float(os.getenv("REQUEST_TIMEOUT", "30"))
    MAX_RETRIES = int(os.getenv("MAX_RETRIES", "3"))
    BACKOFF_FACTOR = float(os.getenv("BACKOFF_FACTOR", "0.7"))
    USER_AGENT = os.getenv("USER_AGENT", "FlightMonitor/1.4 (+github-actions)")
    TIME_BUDGET_SECONDS = int(os.getenv("TIME_BUDGET_SECONDS", "420"))

    HISTORY_PATH = Path(os.getenv("HISTORY_PATH", "data/history.csv"))
    HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)

    # Alertas
    MAX_PRECO_PP = float(os.getenv("MAX_PRECO_PP", "1200"))   # teto por trecho
    ONLY_CAP_BELOW = os.getenv("ONLY_CAP_BELOW", "false").lower() == "true"
    LEG_CAP_ENFORCE_BOTH = os.getenv("LEG_CAP_ENFORCE_BOTH", "false").lower() == "true"
    MIN_DISCOUNT_PCT = float(os.getenv("MIN_DISCOUNT_PCT", "0.25"))

    # Alternativas de volta
    SHOW_RETURN_ALTS = os.getenv("SHOW_RETURN_ALTS", "true").lower() == "true"
    ALT_TOP_N = int(os.getenv("ALT_TOP_N", "3"))
    ALT_MIN_SAVING_BRL = float(os.getenv("ALT_MIN_SAVING_BRL", "0"))

    # Telegram
    TG_PARSE_MODE = os.getenv("TG_PARSE_MODE", "HTML")

    # IA (opcional)
    AI_MODE = os.getenv("AI_MODE", "false").lower() == "true"
    AI_ENGINE = os.getenv("AI_ENGINE", "heuristic")  # heuristic | sklearn
    AI_ADD_TO_ALERT = os.getenv("AI_ADD_TO_ALERT", "false").lower() == "true"
    AI_MIN_UNDERVALUE_PCT = float(os.getenv("AI_MIN_UNDERVALUE_PCT", "0.12"))  # 12% abaixo da previsão

Config.DESTINOS = _build_destinos(Config.ORIGEM)

# ========= Credenciais / Ambiente =========

CLIENT_ID = os.getenv("AMADEUS_API_KEY")
CLIENT_SECRET = os.getenv("AMADEUS_API_SECRET")
ENV = os.getenv("AMADEUS_ENV", "sandbox").strip().lower()
BASE_URL = "https://test.api.amadeus.com" if ENV in ("sandbox", "test") else "https://api.amadeus.com"

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID")

# Expostos para testes
MAX_PRECO_PP = Config.MAX_PRECO_PP
MIN_DISCOUNT_PCT = Config.MIN_DISCOUNT_PCT

# Sessão HTTP
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": Config.USER_AGENT})

CSV_HEADERS = [
    "ts_utc","origem","destino","departure_date","return_date",
    "price_total","currency",
    "leg_out_price","leg_out_airline",
    "leg_ret_price","leg_ret_airline",
    "notified","reason","deeplink"
]

# ========= Util =========

def log(msg: str, level: str = "INFO"):
    icons = {"INFO":"ⓘ", "SUCCESS":"✅", "ERROR":"❌", "WARNING":"⚠️"}
    print(f"[{datetime.utcnow().isoformat()}Z] {icons.get(level, ' ')} {msg}")

def tg_send(text: str):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        log("Telegram não configurado. Pulando envio.", "WARNING")
        return
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": Config.TG_PARSE_MODE, "disable_web_page_preview": True},
            timeout=20
        )
        r.raise_for_status()
        log("Mensagem enviada ao Telegram.", "SUCCESS")
    except requests.RequestException as e:
        log(f"Erro Telegram: {e}", "ERROR")

def google_flights_deeplink(orig: str, dest: str, ida: str, volta: Optional[str]) -> str:
    if volta:
        return f"https://www.google.com/travel/flights?q=Flights%20from%20{orig}%20to%20{dest}%20on%20{ida}%20return%20{volta}"
    return f"https://www.google.com/travel/flights?q=Flights%20from%20{orig}%20to%20{dest}%20on%20{ida}"

# ========= Histórico =========

def load_best_totals() -> Dict[Tuple[str, str], float]:
    best: Dict[Tuple[str,str], float] = {}
    if not Config.HISTORY_PATH.exists():
        return best
    try:
        with Config.HISTORY_PATH.open("r", encoding="utf-8") as f:
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

def append_history_row(row: Dict[str, str]):
    write_header = not Config.HISTORY_PATH.exists()
    try:
        with Config.HISTORY_PATH.open("a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=CSV_HEADERS)
            if write_header:
                w.writeheader()
            w.writerow(row)
    except Exception as e:
        log(f"Erro salvando histórico: {e}", "ERROR")

# ========= Amadeus =========

def get_token() -> str:
    if not CLIENT_ID or not CLIENT_SECRET:
        log("AMADEUS_API_KEY/AMADEUS_API_SECRET ausentes.", "ERROR")
        sys.exit(1)

    for attempt in range(1, Config.MAX_RETRIES + 1):
        try:
            resp = requests.post(
                f"{BASE_URL}/v1/security/oauth2/token",
                data={
                    "grant_type": "client_credentials",
                    "client_id": CLIENT_ID,
                    "client_secret": CLIENT_SECRET,
                },
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

def buscar_one_way(token: str, origem: str, destino: str, departure_date: str, max_offers: int) -> Optional[dict]:
    url = f"{BASE_URL}/v2/shopping/flight-offers"
    params = {
        "originLocationCode": origem,
        "destinationLocationCode": destino,
        "departureDate": departure_date,
        "adults": "1",
        "currencyCode": Config.CURRENCY,
        "max": str(max_offers),
    }
    headers = {"Authorization": f"Bearer {token}"}
    for attempt in range(1, Config.MAX_RETRIES + 1):
        try:
            r = SESSION.get(url, headers=headers, params=params, timeout=Config.REQUEST_TIMEOUT)
            if r.status_code == 200:
                return r.json()
            if r.status_code == 429:
                sleep_s = Config.BACKOFF_FACTOR * attempt + 1.0
                log(f"429 Rate limit (tent {attempt}). Aguardando {sleep_s:.1f}s.", "WARNING")
                time.sleep(sleep_s)
                continue
            log(f"HTTP {r.status_code} {origem}->{destino} {departure_date}: {r.text[:200]}", "WARNING")
        except requests.RequestException as e:
            log(f"Erro rede {origem}->{destino} {departure_date}: {e}", "WARNING")
        time.sleep(Config.BACKOFF_FACTOR * attempt)
    return None

def _extract_airline_name(offer: dict) -> str:
    try:
        seg0 = offer["itineraries"][0]["segments"][0]
        op = seg0.get("operatingCarrierName")
        mk = seg0.get("marketingCarrierName")
        return (op or mk or "").strip() or "N/A"
    except Exception:
        return "N/A"

def _cheapest(offers: Optional[dict]) -> Optional[dict]:
    if not offers or "data" not in offers or not offers["data"]:
        return None
    try:
        cheapest = min(
            offers["data"],
            key=lambda x: _safe_float(x.get("price", {}).get("total"), math.inf),
        )
        cheapest["airline"] = _extract_airline_name(cheapest)
        return cheapest
    except Exception:
        return None

# ========= Regras de alerta =========

def deve_alertar(preco_atual: float, melhor_anterior: Optional[float]) -> Tuple[bool, str]:
    if preco_atual <= MAX_PRECO_PP:
        cap = int(MAX_PRECO_PP) if isinstance(MAX_PRECO_PP, (int, float)) and float(MAX_PRECO_PP).is_integer() else MAX_PRECO_PP
        return True, f"≤ teto {cap}"
    if melhor_anterior is not None and math.isfinite(melhor_anterior) and melhor_anterior > 0:
        queda = (melhor_anterior - preco_atual) / melhor_anterior
        if queda >= MIN_DISCOUNT_PCT:
            return True, f"queda {queda:.0%}"
    return False, "sem queda"

# ========= Datas =========

def _datas_ida(hoje: datetime.date) -> List[str]:
    out = set()
    while len(out) < Config.SAMPLE_DEPARTURES:
        delta = random.randint(Config.DAYS_AHEAD_FROM, Config.DAYS_AHEAD_TO)
        out.add((hoje + timedelta(days=delta)).strftime("%Y-%m-%d"))
    return sorted(out)

def _datas_retorno(ida: str) -> List[str]:
    d = datetime.strptime(ida, "%Y-%m-%d").date()
    return [(d + timedelta(days=n)).strftime("%Y-%m-%d")
            for n in range(Config.STAY_NIGHTS_MIN, Config.STAY_NIGHTS_MAX + 1)]

# ========= IA (opcional) =========

_AI = None
def _ensure_ai_loaded():
    global _AI
    if _AI is not None:
        return _AI
    try:
        from ai_mode import load_predictor
        pred, info = load_predictor(Config.HISTORY_PATH, Config.CURRENCY, Config.AI_ENGINE)
        _AI = (pred, info)
        log(f"IA carregada: {info}")
        return _AI
    except Exception as e:
        log(f"IA desativada (erro ao carregar): {e}", "WARNING")
        _AI = (None, "disabled")
        return _AI

def _ai_insights(orig: str, dest: str, ida: str, volta: str, total: float) -> str:
    if not Config.AI_MODE:
        return ""
    predictor, _info = _ensure_ai_loaded()
    if predictor is None:
        return ""
    try:
        pred = float(predictor.predict_total(orig, dest, ida, volta))
        if pred <= 0 or not math.isfinite(pred):
            return ""
        diff = total - pred
        pct = (diff / pred) * 100.0
        tag = "abaixo" if pct < 0 else "acima"
        return f"IA: {abs(pct):.1f}% {tag} do esperado (pred. {pred:.0f} {Config.CURRENCY})"
    except Exception:
        return ""

# ========= Mensagem =========

def _format_return_alts(melhores_voltas: List[Tuple[str, dict, float]]) -> str:
    if not Config.SHOW_RETURN_ALTS or len(melhores_voltas) <= 1:
        return ""
    base_date, base_offer, base_price = melhores_voltas[0]
    others = []
    for r_date, r_offer, r_price in melhores_voltas[1:Config.ALT_TOP_N + 1]:
        delta = r_price - base_price
        if delta >= Config.ALT_MIN_SAVING_BRL:
            others.append(f"• {r_date}: {r_price:.2f} {Config.CURRENCY} ({r_offer.get('airline','N/A')})")
    if not others:
        return ""
    return "<i>Alternativas de volta:</i>\n" + "\n".join(others)

def _format_msg_roundtrip(orig, dest, date_out, out_offer, ret_date, ret_offer, motivo, deeplink, alts_text="", ia_text=""):
    p_out = _safe_float(out_offer["price"]["total"])
    p_ret = _safe_float(ret_offer["price"]["total"])
    tot = p_out + p_ret
    air_out = out_offer.get("airline", "N/A")
    air_ret = ret_offer.get("airline", "N/A")

    lines = [
        f"✈️ <b>{orig} → {dest}</b>",
        f"• <b>Ida</b> {date_out}: {p_out:.2f} {Config.CURRENCY} ({air_out})",
        f"• <b>Volta</b> {ret_date}: {p_ret:.2f} {Config.CURRENCY} ({air_ret})",
        f"• <b>Total:</b> {tot:.2f} {Config.CURRENCY} — {motivo}",
    ]
    if ia_text:
        lines.append(ia_text)
    if deeplink:
        lines.append(f"🔗 {deeplink}")
    if alts_text:
        lines.append(alts_text)
    return "\n".join(lines)

# ========= Núcleo =========

def process_destino_roundtrip(token: str, origem: str, destino: str, best_totals: Dict[Tuple[str,str], float], deadline: float):
    for ida in _datas_ida(datetime.utcnow().date()):
        if time.time() >= deadline:
            log("Time budget esgotado.", "WARNING")
            return
        time.sleep(Config.REQUEST_DELAY)

        off_ida = _cheapest(buscar_one_way(token, origem, destino, ida, Config.MAX_OFFERS))
        if not off_ida:
            continue
        preco_ida = _safe_float(off_ida["price"]["total"])

        ret_dates = _datas_retorno(ida)
        melhores_voltas: List[Tuple[str, dict, float]] = []
        for r in ret_dates:
            if time.time() >= deadline:
                log("Time budget esgotado (volta).", "WARNING")
                return
            time.sleep(Config.REQUEST_DELAY)

            off_volta = _cheapest(buscar_one_way(token, destino, origem, r, Config.MAX_OFFERS))
            if not off_volta:
                continue
            preco_ret = _safe_float(off_volta["price"]["total"])
            melhores_voltas.append((r, off_volta, preco_ret))

        if not melhores_voltas:
            continue

        melhores_voltas.sort(key=lambda x: x[2])
        ret_date, ret_offer, preco_ret = melhores_voltas[0]
        total = preco_ida + preco_ret

        if Config.LEG_CAP_ENFORCE_BOTH and (preco_ida > Config.MAX_PRECO_PP or preco_ret > Config.MAX_PRECO_PP):
            continue

        # ===== Modo "apenas abaixo do teto nos 2 trechos" =====
        if Config.ONLY_CAP_BELOW:
            if preco_ida <= Config.MAX_PRECO_PP and preco_ret <= Config.MAX_PRECO_PP:
                cap = int(Config.MAX_PRECO_PP) if float(Config.MAX_PRECO_PP).is_integer() else Config.MAX_PRECO_PP
                motivo = f"≤ teto {cap}"
                deeplink = google_flights_deeplink(origem, destino, ida, ret_date)
                ia_text = _ai_insights(origem, destino, ida, ret_date, total)
                msg = _format_msg_roundtrip(origem, destino, ida, off_ida, ret_date, ret_offer, motivo, deeplink,
                                            alts_text=_format_return_alts(melhores_voltas),
                                            ia_text=ia_text)
                tg_send(msg)
                append_history_row({
                    "ts_utc": datetime.utcnow().isoformat()+"Z",
                    "origem": origem, "destino": destino,
                    "departure_date": ida, "return_date": ret_date,
                    "price_total": f"{total:.2f}", "currency": Config.CURRENCY,
                    "leg_out_price": f"{preco_ida:.2f}", "leg_out_airline": off_ida.get("airline","N/A"),
                    "leg_ret_price": f"{preco_ret:.2f}", "leg_ret_airline": ret_offer.get("airline","N/A"),
                    "notified": "1", "reason": motivo,
                    "deeplink": deeplink
                })
            continue

        # ===== Regra histórica padrão =====
        key = (origem, destino)
        melhor_hist = best_totals.get(key, math.inf)
        ok, motivo = deve_alertar(total, melhor_hist if math.isfinite(melhor_hist) else None)

        # ===== Regra IA opcional (só se não alertou ainda) =====
        ia_text = _ai_insights(origem, destino, ida, ret_date, total)
        if Config.AI_MODE and Config.AI_ADD_TO_ALERT and not ok and ia_text:
            # se está X% abaixo do previsto, também alerta
            try:
                # extrai % do texto "IA: 18.4% abaixo ..."
                pct_str = ia_text.split(":")[1].split("%")[0].strip()
                pct_val = abs(float(pct_str))
                if pct_val/100.0 >= Config.AI_MIN_UNDERVALUE_PCT and "abaixo" in ia_text:
                    ok = True
                    motivo = f"IA {pct_val:.0f}% abaixo do previsto"
            except Exception:
                pass

        if ok:
            deeplink = google_flights_deeplink(origem, destino, ida, ret_date)
            msg = _format_msg_roundtrip(origem, destino, ida, off_ida, ret_date, ret_offer, motivo, deeplink,
                                        alts_text=_format_return_alts(melhores_voltas),
                                        ia_text=ia_text)
            tg_send(msg)
            append_history_row({
                "ts_utc": datetime.utcnow().isoformat()+"Z",
                "origem": origem, "destino": destino,
                "departure_date": ida, "return_date": ret_date,
                "price_total": f"{total:.2f}", "currency": Config.CURRENCY,
                "leg_out_price": f"{preco_ida:.2f}", "leg_out_airline": off_ida.get("airline","N/A"),
                "leg_ret_price": f"{preco_ret:.2f}", "leg_ret_airline": ret_offer.get("airline","N/A"),
                "notified": "1", "reason": motivo,
                "deeplink": deeplink
            })
            if total < melhor_hist:
                best_totals[key] = total
        else:
            append_history_row({
                "ts_utc": datetime.utcnow().isoformat()+"Z",
                "origem": origem, "destino": destino,
                "departure_date": ida, "return_date": ret_date,
                "price_total": f"{total:.2f}", "currency": Config.CURRENCY,
                "leg_out_price": f"{preco_ida:.2f}", "leg_out_airline": off_ida.get("airline","N/A"),
                "leg_ret_price": f"{preco_ret:.2f}", "leg_ret_airline": ret_offer.get("airline","N/A"),
                "notified": "0", "reason": "sem queda",
                "deeplink": google_flights_deeplink(origem, destino, ida, ret_date)
            })

# ========= Main =========

def main():
    log(f"Iniciando monitor | ENV={ENV} ({'🚀 PRODUÇÃO' if ENV=='production' else '🧪 SANDBOX'}) | BASE={BASE_URL}")
    if Config.AI_MODE:
        # carrega IA já no início (log informativo)
        _ensure_ai_loaded()

    token = get_token()
    best_totals = load_best_totals()
    deadline = time.time() + Config.TIME_BUDGET_SECONDS

    for dest in Config.DESTINOS:
        if time.time() >= deadline:
            log("Time budget esgotado (loop destinos).", "WARNING")
            break
        log(f"Processando {Config.ORIGEM} → {dest} ...")
        try:
            process_destino_roundtrip(token, Config.ORIGEM, dest, best_totals, deadline)
        except Exception as e:
            log(f"Erro ao processar {Config.ORIGEM}->{dest}: {e}", "ERROR")

    log("Concluído.", "SUCCESS")


if __name__ == "__main__":
    main()