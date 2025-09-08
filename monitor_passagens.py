#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Monitoramento de Passagens Aéreas (Amadeus + Telegram)

- Origem fixa: GYN (Goiânia)
- Destinos padrão: capitais do Brasil (pode sobrescrever via env DESTINOS)
- Busca ida+volta (menor ida + menor volta, não precisa ser mesma cia)
- Envia alertas no Telegram e registra histórico em CSV

Ambiente:
  AMADEUS_API_KEY, AMADEUS_API_SECRET   -> obrigatórios para rodar de verdade
  AMADEUS_ENV=sandbox|production        -> default: sandbox
  AMADEUS_BASE_URL                      -> opcional; se vazio, infere por ENV
  TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID  -> para enviar mensagens
  HISTORY_PATH                          -> caminho CSV (default: data/history.csv)

Parâmetros:
  ORIGEM=GYN
  DESTINOS="GIG,SDU,SSA,FOR,REC,NAT,MCZ,AJU,MAO,BEL,SLZ,THE,BSB,FLN,POA,CWB,CGR,CGB,CNF,VIX,JPA,PMW,PVH,BVB,RBR,GRU,CGH,MCP"
  CURRENCY=BRL
  DAYS_AHEAD_FROM=10
  DAYS_AHEAD_TO=90
  SAMPLE_DEPARTURES=2
  STAY_NIGHTS_MIN=5
  STAY_NIGHTS_MAX=10
  MAX_OFFERS=5
  REQUEST_DELAY=1.2
  MAX_PRECO_PP=1200
  MIN_DISCOUNT_PCT=0.25
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

import requests

# =========================
# Utilidades / Logging
# =========================
def log(msg: str, level: str = "INFO") -> None:
    icons = {"INFO": "ⓘ", "SUCCESS": "✅", "ERROR": "❌", "WARNING": "⚠️", "DEBUG": "🔎"}
    print(f"[{datetime.utcnow().isoformat()}Z] {icons.get(level, ' ')} {msg}")


# =========================
# Config de ambiente
# =========================

# Lista padrão de capitais (códigos IATA principais; RJ/SP com 2 cada, opcional incluir MCP)
_CAPITAIS_DEFAULT = (
    "GIG,SDU,"   # Rio de Janeiro
    "GRU,CGH,"   # São Paulo
    "BSB,"       # Brasília
    "CNF,"       # Belo Horizonte
    "VIX,"       # Vitória
    "CWB,"       # Curitiba
    "FLN,"       # Florianópolis
    "POA,"       # Porto Alegre
    "GYN,"       # Goiânia
    "CGR,"       # Campo Grande
    "CGB,"       # Cuiabá
    "PMW,"       # Palmas
    "RBR,"       # Rio Branco
    "PVH,"       # Porto Velho
    "BVB,"       # Boa Vista
    "MAO,"       # Manaus
    "BEL,"       # Belém
    "MCP,"       # Macapá
    "SLZ,"       # São Luís
    "THE,"       # Teresina
    "FOR,"       # Fortaleza
    "NAT,"       # Natal
    "JPA,"       # João Pessoa
    "REC,"       # Recife
    "MCZ,"       # Maceió
    "AJU,"       # Aracaju
    "SSA"        # Salvador
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

# Origem fixa solicitada
ORIGEM = os.getenv("ORIGEM", "GYN").strip().upper()
DESTINOS = _compute_destinos_from_env(ORIGEM)
CURRENCY = os.getenv("CURRENCY", "BRL")

DAYS_AHEAD_FROM = int(os.getenv("DAYS_AHEAD_FROM", "10"))
DAYS_AHEAD_TO   = int(os.getenv("DAYS_AHEAD_TO", "90"))
SAMPLE_DEPARTURES = int(os.getenv("SAMPLE_DEPARTURES", "2"))
STAY_NIGHTS_MIN   = int(os.getenv("STAY_NIGHTS_MIN", "5"))
STAY_NIGHTS_MAX   = int(os.getenv("STAY_NIGHTS_MAX", "10"))

MAX_OFFERS    = int(os.getenv("MAX_OFFERS", "5"))
REQUEST_DELAY = float(os.getenv("REQUEST_DELAY", "1.2"))

# Regras de alerta (lidas como variáveis de módulo para os testes mexerem)
MAX_PRECO_PP      = float(os.getenv("MAX_PRECO_PP", "1200"))
MIN_DISCOUNT_PCT  = float(os.getenv("MIN_DISCOUNT_PCT", "0.25"))

# Credenciais e endpoints
CLIENT_ID     = os.getenv("AMADEUS_API_KEY")
CLIENT_SECRET = os.getenv("AMADEUS_API_SECRET")

ENV = os.getenv("AMADEUS_ENV", "sandbox").strip().lower()
BASE_URL = os.getenv("AMADEUS_BASE_URL") or (
    "https://test.api.amadeus.com" if ENV in ("sandbox", "test") else "https://api.amadeus.com"
)

# Telegram
TELEGRAM_TOKEN   = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
TG_PARSE_MODE    = os.getenv("TG_PARSE_MODE", "HTML")

# Histórico CSV
HISTORY_PATH = Path(os.getenv("HISTORY_PATH", "data/history.csv"))
HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)

CSV_HEADERS = [
    "ts_utc",
    "origem",
    "destino",
    "departure_date",
    "return_date",
    "price_total",
    "currency",
    "price_outbound",
    "price_inbound",
    "airline_outbound",
    "airline_inbound",
    "notified",
    "reason",
    "score",
]

# Mapeamento simples de códigos para nomes (para humanizar a saída)
AIRLINE_CODE_TO_NAME = {
    "LA": "LATAM Airlines",
    "G3": "GOL Linhas Aéreas",
    "AD": "Azul Linhas Aéreas",
    "VO": "VOEPASS",
    "2Z": "Azul Conecta",
    "PASS": "PASSAREDO",  # fallback
}


# =========================
# Funções exigidas pelos testes
# =========================
def get_token() -> str:
    """
    Obtém token OAuth2 da Amadeus.
    Usa CLIENT_ID/CLIENT_SECRET de variáveis de módulo, para permitir monkeypatch nos testes.
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
        sys.exit(1)


def deve_alertar(preco_atual: float, melhor_anterior: Optional[float]) -> Tuple[bool, str]:
    """
    Regras de alerta:
      1) Se o preço atual <= MAX_PRECO_PP => alerta (motivo: '≤ teto X')
      2) Senão, se houver melhor_anterior e a queda >= MIN_DISCOUNT_PCT => alerta (motivo: 'queda YY%')
      3) Caso contrário => sem alerta
    Lê MAX_PRECO_PP e MIN_DISCOUNT_PCT das variáveis de módulo (testes alteram em runtime).
    """
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


# =========================
# Amadeus helpers
# =========================
def _extract_airline_name(offer: Dict[str, Any]) -> str:
    try:
        seg0 = offer["itineraries"][0]["segments"][0]
    except Exception:
        return "N/A"

    # Tenta operating.carrierCode > carrierCode > marketingCarrierCode > operatingCarrierName
    code = None
    if isinstance(seg0.get("operating"), dict):
        code = seg0["operating"].get("carrierCode")
    code = code or seg0.get("carrierCode") or seg0.get("marketingCarrierCode")
    name = AIRLINE_CODE_TO_NAME.get(code, None) if code else None

    # Alguns payloads trazem "*CarrierName"
    name = name or seg0.get("operatingCarrierName") or seg0.get("marketingCarrierName")
    if code and name:
        return f"{name} ({code})"
    if code:
        return code
    return name or "N/A"


def _cheapest(offers_json: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not offers_json or "data" not in offers_json or not offers_json["data"]:
        return None
    try:
        cheapest = min(
            offers_json["data"],
            key=lambda x: float(x.get("price", {}).get("total", float("inf")))
        )
    except (TypeError, ValueError):
        return None

    cheapest = dict(cheapest)  # copia raso para inserir campos auxiliares
    cheapest["airline"] = _extract_airline_name(cheapest)
    return cheapest


def buscar_one_way(token: str, origem: str, destino: str, date_yyyy_mm_dd: str) -> Optional[Dict[str, Any]]:
    """
    Busca voos one-way na Amadeus.
    Retorna JSON (ou None em erro).
    """
    params = {
        "originLocationCode": origem,
        "destinationLocationCode": destino,
        "departureDate": date_yyyy_mm_dd,
        "adults": "1",
        "currencyCode": CURRENCY,
        "max": str(MAX_OFFERS),
    }
    try:
        r = requests.get(
            f"{BASE_URL}/v2/shopping/flight-offers",
            headers={"Authorization": f"Bearer {token}"},
            params=params,
            timeout=60,
        )
        if r.status_code != 200:
            log(f"Amadeus {origem}->{destino} {date_yyyy_mm_dd} HTTP {r.status_code}: {r.text[:300]}", "WARNING")
            return None
        return r.json()
    except requests.RequestException as e:
        log(f"Erro HTTP one-way {origem}->{destino} {date_yyyy_mm_dd}: {e}", "ERROR")
        return None


# =========================
# Telegram
# =========================
def tg_send(text: str) -> None:
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        log("Telegram não configurado. Pulando envio.", "WARNING")
        return
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": TG_PARSE_MODE, "disable_web_page_preview": True},
            timeout=30,
        )
        if r.status_code != 200:
            log(f"Telegram HTTP {r.status_code}: {r.text[:200]}", "ERROR")
        else:
            log("Mensagem enviada ao Telegram.", "SUCCESS")
    except requests.RequestException as e:
        log(f"Erro ao enviar Telegram: {e}", "ERROR")


# =========================
# Histórico CSV
# =========================
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
    """
    Retorna menor total registrado por (origem, destino).
    """
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


# =========================
# Datas-alvo
# =========================
def _datas_ida() -> List[str]:
    hoje = datetime.utcnow().date()
    out: List[str] = []
    # sorteia SAMPLE_DEPARTURES dentro do range
    for _ in range(max(1, SAMPLE_DEPARTURES)):
        delta = random.randint(DAYS_AHEAD_FROM, DAYS_AHEAD_TO)
        out.append((hoje + timedelta(days=delta)).strftime("%Y-%m-%d"))
    # dedup mantendo ordem
    seen = set()
    dedup = []
    for d in out:
        if d not in seen:
            seen.add(d)
            dedup.append(d)
    return dedup


def _datas_retorno_para(ida: str) -> List[str]:
    """
    Gera datas de retorno com base na ida e no range de noites (min..max).
    """
    d0 = datetime.strptime(ida, "%Y-%m-%d").date()
    ret: List[str] = []
    for n in range(min(STAY_NIGHTS_MIN, STAY_NIGHTS_MAX), max(STAY_NIGHTS_MIN, STAY_NIGHTS_MAX) + 1):
        ret.append((d0 + timedelta(days=n)).strftime("%Y-%m-%d"))
    return ret


# =========================
# Lógica principal: ida + volta combinando menores
# =========================
def _score(preco_total: float, preco_out: float, preco_in: float) -> float:
    """
    Score simples (quanto menor, melhor). Aqui retornamos 0 só para compatibilidade.
    Você pode sofisticar depois (ex.: penalizar 2+ conexões, etc.).
    """
    return 0.0

def _format_msg(origem: str, destino: str, d_ida: str, d_volta: str,
                preco_total: float, moeda: str,
                out_airline: str, in_airline: str,
                motivo: str, score: float) -> str:
    return (
        f"✈️ {origem} → {destino}\n"
        f"• Total: {preco_total:.2f} {moeda} — {motivo} | Score {int(score)}\n"
        f"• Ida {d_ida}: {out_airline}\n"
        f"• Volta {d_volta}: {in_airline}"
    )


def process_destino_roundtrip(token: str, origem: str, destino: str, best_totals: Dict[Tuple[str, str], float]) -> None:
    log(f"🔍 {origem} → {destino} (ida+volta)")

    melhor_global = best_totals.get((origem, destino), float("inf"))
    melhor_combo = None  # tuple (preco_total, moeda, d_ida, d_volta, out_price, in_price, out_air, in_air)

    for d_ida in _datas_ida():
        time.sleep(REQUEST_DELAY)
        js_out = buscar_one_way(token, origem, destino, d_ida)
        cheapest_out = _cheapest(js_out)
        if not cheapest_out:
            continue
        preco_out = float(cheapest_out["price"]["total"])
        moeda = cheapest_out["price"]["currency"]
        out_air = cheapest_out.get("airline", "N/A")

        for d_volta in _datas_retorno_para(d_ida):
            time.sleep(REQUEST_DELAY)
            js_in = buscar_one_way(token, destino, origem, d_volta)
            cheapest_in = _cheapest(js_in)
            if not cheapest_in:
                continue
            preco_in = float(cheapest_in["price"]["total"])
            in_air = cheapest_in.get("airline", "N/A")

            total = preco_out + preco_in

            # mantém combo mais barato desta rodada
            if (melhor_combo is None) or (total < melhor_combo[0]):
                melhor_combo = (total, moeda, d_ida, d_volta, preco_out, preco_in, out_air, in_air)

    if not melhor_combo:
        log(f"Nenhuma combinação encontrada para {origem} → {destino}.", "WARNING")
        # registra linha "sem dados" para termos histórico da tentativa
        _append_history_row({
            "ts_utc": datetime.utcnow().isoformat() + "Z",
            "origem": origem,
            "destino": destino,
            "departure_date": "",
            "return_date": "",
            "price_total": "",
            "currency": CURRENCY,
            "price_outbound": "",
            "price_inbound": "",
            "airline_outbound": "",
            "airline_inbound": "",
            "notified": "0",
            "reason": "sem ofertas",
            "score": "0",
        })
        return

    total, moeda, d_ida, d_volta, preco_out, preco_in, out_air, in_air = melhor_combo

    # regra de alerta com base no melhor total histórico
    alert, motivo = deve_alertar(total, melhor_global)
    notified = False
    score = _score(total, preco_out, preco_in)

    if alert:
        msg = _format_msg(origem, destino, d_ida, d_volta, total, moeda, out_air, in_air, motivo, score)
        log(msg)
        tg_send(msg)
        notified = True

    # grava histórico
    _append_history_row({
        "ts_utc": datetime.utcnow().isoformat() + "Z",
        "origem": origem,
        "destino": destino,
        "departure_date": d_ida,
        "return_date": d_volta,
        "price_total": f"{total:.2f}",
        "currency": moeda,
        "price_outbound": f"{preco_out:.2f}",
        "price_inbound": f"{preco_in:.2f}",
        "airline_outbound": out_air,
        "airline_inbound": in_air,
        "notified": "1" if notified else "0",
        "reason": motivo,
        "score": f"{int(score)}",
    })

    # atualiza melhor total em memória
    if total < melhor_global:
        best_totals[(origem, destino)] = total


# =========================
# Main
# =========================
def main() -> None:
    log(f"Iniciando monitor | ENV={ENV} | BASE={BASE_URL}{' (🚀 PRODUÇÃO)' if ENV == 'production' else ''}")
    token = get_token()
    best_totals = _load_best_totals()

    for destino in DESTINOS:
        try:
            process_destino_roundtrip(token, ORIGEM, destino, best_totals)
        except Exception as e:
            log(f"Erro ao processar {ORIGEM}->{destino}: {e}", "ERROR")

    log("Monitor finalizado.", "SUCCESS")


if __name__ == "__main__":
    main()