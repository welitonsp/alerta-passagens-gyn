#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Monitoramento de Passagens Aéreas (Amadeus + Telegram) — SANDBOX por padrão.
Expõe:
- get_token()
- deve_alertar(preco_atual, melhor_anterior)

Recursos:
- Deal Score baseado em baseline (p25/p50) por rota/antecedência/dia da semana
- Varredura leve por datas vizinhas (±1 dia) quando promissor
- Cooldown de alerta por rota para evitar spam
- Mensagem com link rápido para Google Flights
- Retry/backoff simples em chamadas Amadeus
"""

# ===================== IMPORTS =====================
import os
import sys
import time
import json
import csv
import math
import random
from datetime import datetime, timedelta, date
from pathlib import Path
from typing import List, Tuple, Optional, Dict, Any
import requests

# ===================== LOG =========================
def log(msg: str, level: str = "INFO") -> None:
    icons = {"INFO": "ⓘ", "SUCCESS": "✅", "ERROR": "❌", "WARNING": "⚠️"}
    print(f"[{datetime.utcnow().isoformat()}Z] {icons.get(level,' ')} {msg}")

# ===================== DESTINOS HELPERS ============
def brazil_capitals_iata() -> List[str]:
    # Capitais + hubs comuns (obs: faltou MCP; pode adicionar se quiser)
    return [
        "GIG","SDU","SSA","FOR","REC","NAT","MCZ","AJU",
        "MAO","BEL","SLZ","THE","BSB","FLN","POA","CWB",
        "CGR","CGB","CNF","VIX","JPA","PMW","PVH","BVB",
        "RBR","GYN","GRU","CGH"
    ]

def dedupe_keep_order(seq: List[str]) -> List[str]:
    seen, out = set(), []
    for x in seq:
        x = (x or "").strip().upper()
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

    # Varredura leve por datas vizinhas quando promissor
    CALENDAR_SWEEP_DAYS = int(os.getenv("CALENDAR_SWEEP_DAYS", "1"))  # ±1 dia
    SCORE_ALERT_MIN = int(os.getenv("SCORE_ALERT_MIN", "20"))         # alerta se score >= 20
    SCORE_SWEEP_MIN = int(os.getenv("SCORE_SWEEP_MIN", "10"))         # faz sweep se score >= 10
    COOLDOWN_HOURS  = int(os.getenv("COOLDOWN_HOURS", "12"))          # anti-spam por rota

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

# Histórico & Baseline
DATA_DIR = Path("data"); DATA_DIR.mkdir(parents=True, exist_ok=True)
HISTORY_PATH = DATA_DIR / "history.csv"
BASELINES_PATH = DATA_DIR / "baselines.json"
LAST_ALERTS_PATH = DATA_DIR / "last_alerts.json"  # cooldown por rota

CSV_HEADERS = ["ts_utc","origem","destino","departure_date","price_total","currency","notified","reason","airline","score"]

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
        if float(MAX_PRECO_PP).is_integer():
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

# ===================== BASELINES & SCORE ============
def _d_days(dep: date, collected_utc: date) -> int:
    try:
        return max(0, (dep - collected_utc).days)
    except Exception:
        return 0

def _bucket_ddays(dd: int) -> str:
    # Faixas típicas de compra antecipada
    if dd <= 6: return "0-6"
    if dd <= 13: return "7-13"
    if dd <= 20: return "14-20"
    if dd <= 27: return "21-27"
    if dd <= 34: return "28-34"
    if dd <= 49: return "35-49"
    if dd <= 69: return "50-69"
    return "70-90"

def load_baselines() -> Dict[str, Any]:
    if not BASELINES_PATH.exists():
        return {}
    try:
        return json.loads(BASELINES_PATH.read_text(encoding="utf-8"))
    except Exception as e:
        log(f"Erro ao ler baselines.json: {e}", "WARNING")
        return {}

def lookup_baseline_p25(baselines: Dict[str, Any], origem: str, destino: str, dep_str: str, collected_iso: str) -> Optional[float]:
    if not baselines:
        return None
    try:
        dep = datetime.strptime(dep_str, "%Y-%m-%d").date()
        collected = datetime.fromisoformat(collected_iso.replace("Z","+00:00")).date()
        dd = _d_days(dep, collected)
        dow = dep.weekday()  # 0=segunda
        bucket = _bucket_ddays(dd)
        key = f"{origem}-{destino}-{dow}-{bucket}"
        rec = baselines.get(key)
        if rec and "p25" in rec and isinstance(rec["p25"], (int,float)):
            return float(rec["p25"])
    except Exception:
        pass
    return None

def deal_score(price: float, baseline_p25: Optional[float], stops: int = 0, extra_minutes: int = 0,
               red_eye: bool = False, preferred: bool = False) -> int:
    if not baseline_p25 or baseline_p25 <= 0:
        return 0
    score = 100 * (baseline_p25 - price) / baseline_p25
    score -= 10 * max(0, int(stops))
    score -= 0.5 * max(0, int(extra_minutes)) / 60.0
    if red_eye: score -= 5
    if preferred: score += 3
    return int(max(0, min(100, round(score))))

# ===================== RESTO DO MONITOR =============
def telegram_link(origem: str, destino: str, date_str: str) -> str:
    # Link simples para pesquisa direta no Google Flights (sem API externa)
    q = f"voos {origem} para {destino} {date_str}"
    return f"https://www.google.com/travel/flights?hl=pt-BR&q={requests.utils.quote(q)}"

def enviar_telegram(text: str, link: Optional[str] = None):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        log("Telegram não configurado (TOKEN/CHAT_ID ausentes).", "WARNING")
        return
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text}
    if link:
        payload["reply_markup"] = {
            "inline_keyboard": [[{"text": "🔎 Ver no Google Flights", "url": link}]]
        }
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json=payload,
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

def _request_with_retry(method: str, url: str, **kw) -> requests.Response:
    # Backoff simples para sandbox
    tries = 3
    delay = 2.0
    last_exc = None
    for i in range(tries):
        try:
            r = requests.request(method, url, timeout=kw.pop("timeout", 60), **kw)
            # 429/5xx -> tenta novamente
            if r.status_code in (429, 500, 502, 503, 504):
                time.sleep(delay); delay *= 2; continue
            return r
        except requests.RequestException as e:
            last_exc = e
            time.sleep(delay); delay *= 2
    if last_exc:
        raise last_exc
    raise RuntimeError("Falha HTTP desconhecida")

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
        r = _request_with_retry(
            "GET",
            f"{BASE_URL}/v2/shopping/flight-offers",
            headers={"Authorization": f"Bearer {token}"},
            params=params,
        )
        if r.status_code != 200:
            log(f"HTTP {r.status_code} {origem}->{destino} {date_str}: {r.text[:200]}", "ERROR")
            return None
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

def _duration_minutes(offer: Dict[str, Any]) -> int:
    # transforma "PT7H35M" em minutos, somando itinerários
    try:
        dur = offer["itineraries"][0]["duration"]  # ex: PT7H35M
        h, m = 0, 0
        s = dur.replace("PT", "")
        if "H" in s:
            h = int(s.split("H")[0])
            s = s.split("H")[1]
        if "M" in s:
            m = int(s.split("M")[0])
        return h*60 + m
    except Exception:
        return 0

def _stops(offer: Dict[str, Any]) -> int:
    try:
        segs = offer["itineraries"][0]["segments"]
        return max(0, len(segs) - 1)
    except Exception:
        return 0

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
    # datas concentradas em janelas doçes de compra (21, 28, 35, 45, 60, 90)
    prefer = [21, 28, 35, 45, 60, 90]
    for _ in range(Config.SAMPLE_DEPARTURES):
        base = random.choice(prefer)
        delta = min(max(Config.DAYS_AHEAD_FROM, base), Config.DAYS_AHEAD_TO)
        out.append((today + timedelta(days=delta)).strftime("%Y-%m-%d"))
    # fallback aleatório se sample_departures > len(prefer)
    while len(out) < Config.SAMPLE_DEPARTURES:
        rnd = random.randint(Config.DAYS_AHEAD_FROM, Config.DAYS_AHEAD_TO)
        out.append((today + timedelta(days=rnd)).strftime("%Y-%m-%d"))
    return out

def resumo_msg(origem: str, destino: str, date_str: str, price: float, currency: str, airline: str,
               motivo: str, score: int, p25: Optional[float]) -> str:
    p25_txt = f" | p25≈{p25:.0f}" if p25 else ""
    return f"✈️ {origem} → {destino} {date_str}: {price:.2f} {currency} ({airline}) — {motivo} | Score {score}{p25_txt}"

def _cooldown_ok(origem: str, destino: str) -> bool:
    if not LAST_ALERTS_PATH.exists():
        return True
    try:
        data = json.loads(LAST_ALERTS_PATH.read_text(encoding="utf-8"))
        key = f"{origem}-{destino}"
        last_ts = data.get(key)
        if not last_ts:
            return True
        last = datetime.fromisoformat(last_ts.replace("Z", "+00:00"))
        return (datetime.utcnow() - last) >= timedelta(hours=Config.COOLDOWN_HOURS)
    except Exception:
        return True

def _mark_alert(origem: str, destino: str):
    try:
        data = {}
        if LAST_ALERTS_PATH.exists():
            data = json.loads(LAST_ALERTS_PATH.read_text(encoding="utf-8"))
        data[f"{origem}-{destino}"] = datetime.utcnow().isoformat() + "Z"
        LAST_ALERTS_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        log(f"Erro ao marcar cooldown: {e}", "WARNING")

def _try_sweep_dates(token: str, origem: str, destino: str, base_date: str) -> List[Tuple[Dict[str,Any], Dict[str,Any], str]]:
    """Busca datas vizinhas ±CALENDAR_SWEEP_DAYS, retornando lista de (offer, dicts, date) promissoras"""
    D = Config.CALENDAR_SWEEP_DAYS
    if D <= 0: 
        return []
    base = datetime.strptime(base_date, "%Y-%m-%d").date()
    results = []
    for dd in range(1, D+1):
        for side in (-1, +1):
            d = base + timedelta(days=side*dd)
            ds = d.strftime("%Y-%m-%d")
            time.sleep(Config.REQUEST_DELAY)
            payload = buscar_passagens(token, origem, destino, ds)
            if not payload: 
                continue
            ch, di = find_cheapest_offer(payload)
            if ch:
                results.append((ch, di, ds))
    return results

def process_destino(token: str, origem: str, destino: str, melhores: Dict[Tuple[str,str], float], baselines: Dict[str,Any]):
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
        dep_real = cheapest.get("itineraries", [{}])[0].get("segments", [{}])[0].get("departure", {}).get("at", date_str)[:10]

        alert, motivo = deve_alertar(price, melhor_anterior)

        # Baseline/score
        collected_iso = datetime.utcnow().isoformat() + "Z"
        p25 = lookup_baseline_p25(baselines, origem, destino, dep_real, collected_iso)
        stops = _stops(cheapest)
        dur_min = _duration_minutes(cheapest)
        red_eye = False  # pode sofisticar (voos que partem 22h–06h)
        score = deal_score(price, p25, stops=stops, extra_minutes=0, red_eye=red_eye, preferred=False)

        # Varredura ±1 dia se candidato promissor
        if score >= Config.SCORE_SWEEP_MIN and Config.CALENDAR_SWEEP_DAYS > 0:
            for ch2, di2, ds2 in _try_sweep_dates(token, origem, destino, dep_real):
                price2 = float(ch2["price"]["total"])
                if price2 < price:
                    # substitui pelo melhor da vizinhança
                    cheapest, dictionaries = ch2, di2
                    price, dep_real = price2, ds2
                    currency = ch2["price"].get("currency", Config.CURRENCY)
                    airline = extract_airline(ch2, di2)
                    p25 = lookup_baseline_p25(baselines, origem, destino, dep_real, collected_iso)
                    score = deal_score(price, p25, stops=_stops(ch2), extra_minutes=0)

        # Mensagem & cooldown
        notified = False
        msg = resumo_msg(origem, destino, dep_real, price, currency, airline, motivo if alert else "observação", score, p25)
        link = telegram_link(origem, destino, dep_real)

        if alert or score >= Config.SCORE_ALERT_MIN or (p25 and price <= p25):
            if _cooldown_ok(origem, destino):
                enviar_telegram(msg, link=link)
                _mark_alert(origem, destino)
                notified = True
            else:
                log("⚠️ Cooldown ativo: evitando alerta repetido.", "WARNING")

        append_history_row({
            "ts_utc": collected_iso,
            "origem": origem,
            "destino": destino,
            "departure_date": dep_real,
            "price_total": f"{price:.2f}",
            "currency": currency,
            "notified": "1" if notified else "0",
            "reason": ("alerta: " + motivo) if notified else "sem alerta",
            "airline": airline,
            "score": str(score),
        })

        if price < melhor_anterior:
            melhor_anterior = price
            melhores[best_key] = price

def main():
    banner_env = "🚀 PRODUÇÃO" if ENV == "production" else "🔧 SANDBOX"
    log(f"Iniciando monitor | ENV={ENV} ({banner_env}) | BASE={BASE_URL}")
    token = get_token()
    baselines = load_baselines()
    melhores: Dict[Tuple[str,str], float] = {}
    for dest in Config.DESTINOS:
        process_destino(token, Config.ORIGEM, dest, melhores, baselines)
    log("Monitoramento concluído.", "SUCCESS")

if __name__ == "__main__":
    main()
