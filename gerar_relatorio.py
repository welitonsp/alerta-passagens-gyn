#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Gera um relatório diário (UTC) com os menores preços por rota encontrados
no dia anterior e envia em mensagens chunkadas para o Telegram.

Lê: data/history.csv
Usa secrets/vars: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, HISTORY_PATH (opcional),
REPORT_DAYS_BACK (opcional, default=1), TG_PARSE_MODE (Markdown|MarkdownV2, default=Markdown)
"""

from __future__ import annotations
import csv
import os
import sys
import math
import time
import requests
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Any, List, Tuple

# -------------------------
# Config
# -------------------------
HISTORY_PATH = Path(os.getenv("HISTORY_PATH", "data/history.csv"))
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
REPORT_DAYS_BACK = int(os.getenv("REPORT_DAYS_BACK", "1"))
TG_PARSE_MODE = os.getenv("TG_PARSE_MODE", "Markdown")  # "Markdown" (mais simples) ou "MarkdownV2"

# Limites do Telegram
TG_MAX_LEN = 4000
TG_SAFETY = 200  # margem de segurança
TG_CHUNK = TG_MAX_LEN - TG_SAFETY

# -------------------------
# Utils
# -------------------------
def log(msg: str):
    print(f"[{datetime.utcnow().isoformat()}Z] {msg}")

def escape_mdv2(text: str) -> str:
    """Escapa caracteres especiais do MarkdownV2 (se for o modo selecionado)."""
    if TG_PARSE_MODE != "MarkdownV2": 
        return text
    # caracteres que precisam de escape no MarkdownV2
    chars = r"_*[]()~`>#+-=|{}.!"
    for ch in chars:
        text = text.replace(ch, f"\\{ch}")
    return text

def tg_send(text: str) -> None:
    """Envia um único texto ao Telegram."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        log("Telegram não configurado; exibindo no stdout:")
        print(text)
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": TG_PARSE_MODE,
        "disable_web_page_preview": True,
    }
    r = requests.post(url, json=payload, timeout=20)
    if r.status_code != 200:
        log(f"Falha ao enviar Telegram: {r.status_code} {r.text[:300]}")
    else:
        log("Relatório enviado para Telegram.")

def tg_send_chunked(text: str) -> None:
    """Quebra em pedaços menores se ultrapassar o limite do Telegram."""
    if len(text) <= TG_CHUNK:
        tg_send(text)
        return
    i = 0
    parts: List[str] = []
    while i < len(text):
        parts.append(text[i:i+TG_CHUNK])
        i += TG_CHUNK
    for idx, part in enumerate(parts, 1):
        tg_send(f"{part}\n\n({idx}/{len(parts)})")
        time.sleep(0.8)

def google_flights_link(ori: str, dest: str, date_yyyy_mm_dd: str) -> str:
    # Link simples (one-way, 1 adulto, BRL)
    return (
        "https://www.google.com/travel/flights?"
        f"hl=pt-BR&curr=BRL&flt={ori}.{dest}.{date_yyyy_mm_dd};"
        "tt=o"
    )

# -------------------------
# Leitura & agregação
# -------------------------
Row = Dict[str, Any]

def load_rows_for_day(day_utc: datetime.date) -> List[Row]:
    """Carrega linhas do CSV cujo ts_utc seja do dia 'day_utc'."""
    rows: List[Row] = []
    if not HISTORY_PATH.exists():
        log(f"Arquivo não encontrado: {HISTORY_PATH}")
        return rows

    with HISTORY_PATH.open("r", encoding="utf-8") as f:
        rd = csv.DictReader(f)
        for r in rd:
            ts_raw = r.get("ts_utc") or r.get("ts") or ""
            try:
                ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
            except Exception:
                continue
            if ts.date() == day_utc:
                rows.append(r)
    return rows

def summarize_by_route(rows: List[Row]) -> Dict[str, Dict[str, Any]]:
    """
    Retorna dict por rota "ORI-DEST":
      - best: dict com melhor registro (menor preço)
      - avg: média simples de preços do dia para a rota
      - count: quantidade de observações
    """
    buckets: Dict[str, List[Row]] = defaultdict(list)
    for r in rows:
        ori, dest = r.get("origem"), r.get("destino")
        price_s = r.get("price_total", "")
        if not ori or not dest or not price_s:
            continue
        try:
            price = float(price_s)
        except Exception:
            continue
        key = f"{ori}-{dest}"
        # normaliza campo para uso depois
        r["_price"] = price
        buckets[key].append(r)

    out: Dict[str, Dict[str, Any]] = {}
    for key, lst in buckets.items():
        # menor preço no dia
        best = min(lst, key=lambda rr: rr["_price"])
        media = sum(rr["_price"] for rr in lst) / max(1, len(lst))
        out[key] = {
            "best": best,
            "avg": media,
            "count": len(lst),
        }
    return out

# -------------------------
# Formatação do relatório
# -------------------------
def format_route_line(i: int, key: str, info: Dict[str, Any], use_mdv2: bool) -> str:
    best = info["best"]
    ori, dest = best.get("origem", "???"), best.get("destino", "???")
    price = best.get("_price", 0.0)
    curr = best.get("currency", "BRL")
    dep = best.get("departure_date") or best.get("data_voo") or ""
    airline = best.get("airline", "") or "N/A"
    avg = info["avg"]
    count = info["count"]
    link = google_flights_link(ori, dest, dep) if dep and ori and dest else ""

    if TG_PARSE_MODE == "MarkdownV2" and use_mdv2:
        title = escape_mdv2(f"{i:02d}. {ori}→{dest}  {price:.2f} {curr}")
        rest = escape_mdv2(f"• Voo: {dep}  • CIA: {airline}  • Média: {avg:.2f}  (n={count})")
        if link:
            link_line = escape_mdv2(link)
            return f"{title}\n{rest}\n{link_line}"
        return f"{title}\n{rest}"

    # Markdown simples (default, menos chato com escapes)
    title = f"*{i:02d}. {ori}→{dest}*  `{price:.2f} {curr}`"
    rest = f"• Voo: `{dep}`  • CIA: `{airline}`  • Média: `{avg:.2f}`  (n={count})"
    if link:
        return f"{title}\n{rest}\n{link}"
    return f"{title}\n{rest}"

def build_report_text() -> str:
    today = datetime.utcnow().date()
    target_day = today - timedelta(days=REPORT_DAYS_BACK)
    rows = load_rows_for_day(target_day)
    if not rows:
        return f"📊 *Relatório de Preços*\n\nNenhum dado encontrado para {target_day:%d/%m/%Y}."

    summary = summarize_by_route(rows)
    # ranking por menor preço
    ranking: List[Tuple[str, Dict[str, Any]]] = sorted(
        summary.items(),
        key=lambda kv: kv[1]["best"]["_price"]
    )

    header = (
        f"📊 *Relatório de Preços de Passagens*\n"
        f"🗓️ Dia (UTC): *{target_day:%d/%m/%Y}*\n"
        f"Rotas analisadas: *{len(ranking)}*\n\n"
    )

    lines: List[str] = [header]
    for i, (key, info) in enumerate(ranking, 1):
        lines.append(format_route_line(i, key, info, use_mdv2=True))

    # agregados (menor geral + média das TOP N)
    menor_preco = ranking[0][1]["best"]["_price"]
    top_n = min(10, len(ranking))
    media_top = sum(ranking[j][1]["best"]["_price"] for j in range(top_n)) / top_n
    footer = (
        "\n—\n"
        f"Menor do dia: *{menor_preco:.2f}*\n"
        f"Média das TOP {top_n}: *{media_top:.2f}*\n"
    )
    lines.append(footer)
    return "\n".join(lines)

# -------------------------
# Main
# -------------------------
def main() -> None:
    try:
        text = build_report_text()
        tg_send_chunked(text)
        print(text)  # também deixa no log do Actions
    except Exception as e:
        log(f"Erro ao gerar/enviar relatório: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()