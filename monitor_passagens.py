#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Monitoramento de Passagens Aéreas (Amadeus Sandbox + Telegram)
- Origem fixa: GYN (Goiânia)
- Destinos: configuráveis via env (por padrão todas capitais/hubs principais)
- Busca ida+volta com companhias possivelmente diferentes em cada perna
- Seleciona a combinação mais barata (ida + volta)
- Envia alerta no Telegram conforme regras
- Registra histórico em CSV
"""

import os
import sys
import csv
import json
import time
import random
import requests
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Tuple, Optional, List, Any

# ---------------------------
# Config primária (fora da classe) — evita NameError em compreensões dentro de classe
# ---------------------------

# Origem fixa em Goiânia:
ORIGEM_ENV = "GYN"  # força Goiânia como origem

# Lista prática (capitais + hubs relevantes no mesmo mercado)
DEFAULT_CAPITAIS = (
    "RBR,MAO,MCP,BEL,PVH,BVB,PMW,MCZ,SSA,FOR,SLZ,JPA,REC,THE,NAT,AJU,BSB,"
    "CGB,CGR,VIX,CNF,SDU,GIG,CGH,GRU,CWB,POA,FLN"
)

_destinos_raw = os.getenv("DESTINOS", DEFAULT_CAPITAIS)
_destinos_list = [d.strip().upper() for d in _destinos_raw.split(",") if d.strip()]
# dedupe preservando ordem e removendo a origem
DESTINOS_ENV = [d for d in dict.fromkeys(_destinos_list) if d != ORIGEM_ENV]

# ---------------------------
# Classe de configuração (consome os valores já calculados acima)
# ---------------------------
class Config:
    ORIGEM = ORIGEM_ENV
    DESTINOS = DESTINOS_ENV

    # datas (amostragem)
    DAYS_AHEAD_FROM = int(os.getenv("DAYS_AHEAD_FROM", "10"))
    DAYS_AHEAD_TO   = int(os.getenv("DAYS_AHEAD_TO", "90"))
    SAMPLE_DEPARTURES = int(os.getenv("SAMPLE_DEPARTURES", "2"))  # quantas datas de ida por destino

    # round-trip (noites no destino)
    ROUND_TRIP = os.getenv("ROUND_TRIP", "1") in ("1", "true", "True")
    STAY_NIGHTS_MIN = int(os.getenv("STAY_NIGHTS_MIN", "5"))
    STAY_NIGHTS_MAX = int(os.getenv("STAY_NIGHTS_MAX", "10"))
    SAMPLE_STAYS    = int(os.getenv("SAMPLE_STAYS", "0"))  # 0 = testa todas as noites; 2 = testa 2 valores

    # API Amadeus / limites
    AMADEUS_ENV = os.getenv("AMADEUS_ENV", "sandbox").strip().lower()
    BASE_URL = "https://test.api.amadeus.com" if AMADEUS_ENV in ("sandbox", "test") else "https://api.amadeus.com"
    MAX_OFFERS    = int(os.getenv("MAX_OFFERS", "5"))
    REQUEST_DELAY = float(os.getenv("REQUEST_DELAY", "1.2"))

    # regras de alerta (aplicadas ao TOTAL ida+volta)
    MAX_PRECO_PP        = float(os.getenv("MAX_PRECO_PP", "1200"))
    MIN_DISCOUNT_PCT    = float(os.getenv("MIN_DISCOUNT_PCT", "0.25"))

    # moeda
    CURRENCY = os.getenv("CURRENCY", "BRL").strip().upper()

    # paths
    HISTORY_PATH     = Path(os.getenv("HISTORY_PATH", "data/history.csv"))
    TOKEN_CACHE_PATH = Path(os.getenv("TOKEN_CACHE_PATH", "data/amadeus_token.json"))

    # logging
    LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()


# ---------------------------
# Credenciais / Telegram
# ---------------------------
CLIENT_ID = os.getenv("AMADEUS_API_KEY")
CLIENT_SECRET = os.getenv("AMADEUS_API_SECRET")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID")


# ---------------------------
# Utilitários
# ---------------------------
def log(msg: str, level: str = "INFO") -> None:
    levels = ["DEBUG", "INFO", "WARNING", "ERROR"]
    if level not in levels:
        level = "INFO"
    # simples filtro por nível
    if levels.index(level) < levels.index(Config.LOG_LEVEL):
        return
    icons = {"DEBUG":"🐞","INFO":"ⓘ","WARNING":"⚠️","ERROR":"❌"}
    print(f"[{datetime.utcnow().isoformat()}Z] {icons.get(level,' ')} {msg}")

def ensure_dirs() -> None:
    Config.HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    Config.TOKEN_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)

def tg_send(text: str) -> None:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        log("Telegram não configurado. Pulando envio.", "WARNING")
        return
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": int(TELEGRAM_CHAT_ID), "text": text, "parse_mode": "HTML", "disable_web_page_preview": True},
            timeout=20,
        )
        if r.status_code >= 400:
            log(f"Falha ao enviar Telegram: {r.status_code} {r.text[:200]}", "WARNING")
        else:
            log("Mensagem enviada ao Telegram.", "INFO")
    except Exception as e:
        log(f"Erro Telegram: {e}", "WARNING")


# ---------------------------
# Token cache
# ---------------------------
def _read_token_cache() -> Optional[Dict[str, Any]]:
    try:
        if Config.TOKEN_CACHE_PATH.exists():
            with Config.TOKEN_CACHE_PATH.open("r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return None

def _write_token_cache(token: str, expires_in: int) -> None:
    try:
        data = {"access_token": token, "expires_at": (datetime.utcnow() + timedelta(seconds=expires_in - 30)).isoformat() + "Z"}
        with Config.TOKEN_CACHE_PATH.open("w", encoding="utf-8") as f:
            json.dump(data, f)
    except Exception as e:
        log(f"Não foi possível gravar cache do token: {e}", "DEBUG")


def get_token() -> str:
    """Obtém (ou reusa) token OAuth2 da Amadeus."""
    ensure_dirs()

    # cache
    cache = _read_token_cache()
    if cache:
        try:
            exp = datetime.fromisoformat(cache["expires_at"].replace("Z", "+00:00"))
            if datetime.utcnow() < exp and cache.get("access_token"):
                return cache["access_token"]
        except Exception:
            pass

    if not CLIENT_ID or not CLIENT_SECRET:
        log("AMADEUS_API_KEY/AMADEUS_API_SECRET ausentes.", "ERROR")
        sys.exit(1)

    try:
        resp = requests.post(
            f"{Config.BASE_URL}/v1/security/oauth2/token",
            data={
                "grant_type": "client_credentials",
                "client_id": CLIENT_ID,
                "client_secret": CLIENT_SECRET,
            },
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        token = data["access_token"]
        expires_in = int(data.get("expires_in", 1800))
        _write_token_cache(token, expires_in)
        return token
    except requests.RequestException as e:
        log(f"Falha ao obter token: {e}", "ERROR")
        sys.exit(1)


# ---------------------------
# Amadeus API helpers
# ---------------------------
def _carriers_dict(offers: Dict[str, Any]) -> Dict[str, str]:
    """Extrai dicionário de cias do payload ('dictionaries' → 'carriers')."""
    try:
        return offers.get("dictionaries", {}).get("carriers", {}) or {}
    except Exception:
        return {}

def _price_total(offer: Dict[str, Any]) -> Optional[float]:
    try:
        return float(offer["price"]["total"])
    except Exception:
        return None

def _airline_from_segments(offer: Dict[str, Any], carriers: Dict[str, str]) -> str:
    """Tenta extrair nome da cia da primeira perna; prioriza operatingCarrier > marketingCarrier."""
    try:
        seg = offer["itineraries"][0]["segments"][0]
        op = seg.get("operating", {}).get("carrierCode") or seg.get("operatingCarrierCode") or seg.get("carrierCode")
        mk = seg.get("carrierCode") or seg.get("marketingCarrierCode")
        code = (op or mk or "").strip()
        if code and carriers.get(code):
            return carriers[code]
        return code or "N/A"
    except Exception:
        return "N/A"

def buscar_one_way(token: str, origem: str, destino: str, data: str, max_offers: int) -> Optional[Dict[str, Any]]:
    """Chama /v2/shopping/flight-offers (one-way)"""
    params = {
        "originLocationCode": origem,
        "destinationLocationCode": destino,
        "departureDate": data,
        "adults": "1",
        "currencyCode": Config.CURRENCY,
        "max": str(max_offers),
    }
    headers = {"Authorization": f"Bearer {token}"}
    try:
        r = requests.get(f"{Config.BASE_URL}/v2/shopping/flight-offers", headers=headers, params=params, timeout=60)
        # Em sandbox, erros 5xx podem ocorrer com frequência
        if r.status_code >= 500:
            log(f"Sandbox 5xx em {origem}->{destino} {data}: {r.status_code}", "WARNING")
            return None
        r.raise_for_status()
        return r.json()
    except requests.RequestException as e:
        log(f"Erro buscar_one_way {origem}->{destino} {data}: {e}", "WARNING")
        return None

def cheapest_offer(offers: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not offers or not offers.get("data"):
        return None
    best = None
    best_price = float("inf")
    carriers = _carriers_dict(offers)
    for of in offers["data"]:
        p = _price_total(of)
        if p is None:
            continue
        if p < best_price:
            best_price = p
            best = of
    if best:
        # anota cia "amigável"
        best["_airline_name"] = _airline_from_segments(best, carriers)
        best["_currency"] = best.get("price", {}).get("currency", Config.CURRENCY)
    return best


# ---------------------------
# Amostragem de datas
# ---------------------------
def gerar_datas_ida(n: int) -> List[str]:
    hoje = datetime.utcnow().date()
    out = set()
    tentativas = 0
    while len(out) < max(1, n) and tentativas < 20:
        delta = random.randint(Config.DAYS_AHEAD_FROM, Config.DAYS_AHEAD_TO)
        out.add((hoje + timedelta(days=delta)).strftime("%Y-%m-%d"))
        tentativas += 1
    return sorted(out)

def gerar_noites() -> List[int]:
    nights = list(range(Config.STAY_NIGHTS_MIN, Config.STAY_NIGHTS_MAX + 1))
    if Config.SAMPLE_STAYS and Config.SAMPLE_STAYS > 0 and Config.SAMPLE_STAYS < len(nights):
        random.seed(42)
        nights = random.sample(nights, Config.SAMPLE_STAYS)
    return sorted(nights)


# ---------------------------
# Regras de alerta
# ---------------------------
def deve_alertar(preco_atual: float, melhor_anterior: Optional[float]) -> Tuple[bool, str]:
    """Retorna (alerta?, motivo) — usado nos testes unitários."""
    if preco_atual <= Config.MAX_PRECO_PP:
        return True, f"≤ teto {int(Config.MAX_PRECO_PP)}"
    if melhor_anterior is not None and melhor_anterior < float("inf"):
        desconto = (melhor_anterior - preco_atual) / melhor_anterior
        if desconto >= Config.MIN_DISCOUNT_PCT:
            return True, f"queda {desconto:.0%}"
    return False, "sem queda relevante"


# ---------------------------
# Histórico CSV
# ---------------------------
CSV_HEADERS = [
    "ts_utc","origem","destino","departure_date","return_date",
    "price_total","currency",
    "price_outbound","airline_outbound",
    "price_inbound","airline_inbound",
    "notified","reason"
]

def load_best_totals() -> Dict[Tuple[str, str], float]:
    best: Dict[Tuple[str, str], float] = {}
    if not Config.HISTORY_PATH.exists():
        return best
    try:
        with Config.HISTORY_PATH.open("r", encoding="utf-8") as f:
            rd = csv.DictReader(f)
            for row in rd:
                try:
                    key = (row["origem"], row["destino"])
                    tot = float(row["price_total"])
                    if key not in best or tot < best[key]:
                        best[key] = tot
                except Exception:
                    continue
    except Exception as e:
        log(f"Erro lendo histórico: {e}", "WARNING")
    return best

def append_history_row(row: Dict[str, Any]) -> None:
    ensure_dirs()
    write_header = not Config.HISTORY_PATH.exists()
    try:
        with Config.HISTORY_PATH.open("a", newline="", encoding="utf-8") as f:
            wr = csv.DictWriter(f, fieldnames=CSV_HEADERS)
            if write_header:
                wr.writeheader()
            wr.writerow(row)
    except Exception as e:
        log(f"Erro escrevendo histórico: {e}", "ERROR")


# ---------------------------
# Processamento por destino (round-trip com pernas independentes)
# ---------------------------
def process_destino_roundtrip(token: str, origem: str, destino: str, best_totals: Dict[Tuple[str, str], float]) -> None:
    log(f"🔎 {origem} → {destino} (ida+volta)", "INFO")
    datas_ida = gerar_datas_ida(Config.SAMPLE_DEPARTURES)
    noites = gerar_noites()

    best_combo = None  # (total, ida_date, volta_date, offer_ida, offer_volta)

    for d_ida in datas_ida:
        time.sleep(Config.REQUEST_DELAY)
        ida_offers = buscar_one_way(token, origem, destino, d_ida, Config.MAX_OFFERS)
        ida_best = cheapest_offer(ida_offers)
        if not ida_best:
            log(f"Nenhuma ida encontrada {origem}->{destino} {d_ida}", "DEBUG")
            continue
        preco_ida = _price_total(ida_best) or float("inf")

        for nights in noites:
            d_volta = (datetime.fromisoformat(d_ida) + timedelta(days=nights)).date().strftime("%Y-%m-%d")
            time.sleep(Config.REQUEST_DELAY)
            volta_offers = buscar_one_way(token, destino, origem, d_volta, Config.MAX_OFFERS)
            volta_best = cheapest_offer(volta_offers)
            if not volta_best:
                log(f"Nenhuma volta encontrada {destino}->{origem} {d_volta}", "DEBUG")
                continue
            preco_volta = _price_total(volta_best) or float("inf")
            total = preco_ida + preco_volta

            if (best_combo is None) or (total < best_combo[0]):
                best_combo = (total, d_ida, d_volta, ida_best, volta_best)

    if not best_combo:
        log(f"❌ Sem combinações para {origem}→{destino} nas janelas testadas.", "INFO")
        return

    total, d_ida, d_volta, ida_best, volta_best = best_combo
    cur = (ida_best.get("price", {}) or {}).get("currency", Config.CURRENCY)
    cia_ida = ida_best.get("_airline_name", "N/A")
    cia_volta = volta_best.get("_airline_name", "N/A")

    # regra de alerta
    key = (origem, destino)
    prev_best = best_totals.get(key, float("inf"))
    alert, reason = deve_alertar(total, prev_best)
    notified = False
    msg = (
        f"✈️ <b>{origem} → {destino}</b>\n"
        f"• Ida {d_ida}: {(_price_total(ida_best) or 0):.2f} {cur} ({cia_ida})\n"
        f"• Volta {d_volta}: {(_price_total(volta_best) or 0):.2f} {cur} ({cia_volta})\n"
        f"• <b>Total</b>: {total:.2f} {cur} — {reason}"
    )
    log(msg, "INFO")
    if alert:
        tg_send(msg)
        notified = True

    # histórico
    append_history_row({
        "ts_utc": datetime.utcnow().isoformat() + "Z",
        "origem": origem,
        "destino": destino,
        "departure_date": d_ida,
        "return_date": d_volta,
        "price_total": f"{total:.2f}",
        "currency": cur,
        "price_outbound": f"{(_price_total(ida_best) or 0):.2f}",
        "airline_outbound": cia_ida,
        "price_inbound": f"{(_price_total(volta_best) or 0):.2f}",
        "airline_inbound": cia_volta,
        "notified": "1" if notified else "0",
        "reason": reason
    })

    # atualiza melhor total
    if total < prev_best:
        best_totals[key] = total


# ---------------------------
# Main
# ---------------------------
def main() -> None:
    log(f"Iniciando monitor | ENV={Config.AMADEUS_ENV} | BASE={Config.BASE_URL}", "INFO")
    ensure_dirs()
    token = get_token()
    best_totals = load_best_totals()

    for dest in Config.DESTINOS:
        try:
            process_destino_roundtrip(token, Config.ORIGEM, dest, best_totals)
        except Exception as e:
            log(f"Erro processando {Config.ORIGEM}->{dest}: {e}", "WARNING")

    log("Monitoramento concluído.", "INFO")


# Exporta funções usadas nos testes
__all__ = [
    "get_token",
    "deve_alertar",
    "buscar_one_way",
    "cheapest_offer",
    "process_destino_roundtrip",
    "Config"
]

if __name__ == "__main__":
    main()