#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Relatório diário (UTC) com ranking por rota (menor preço + média),
inclui companhia aérea e link para Google Flights.

Lê: data/history.csv
Envios: Telegram (chunked)
"""

from __future__ import annotations
import csv
import os
import sys
import time
import requests
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Any, List, Tuple

HISTORY_PATH = Path(os.getenv("HISTORY_PATH", "data/history.csv"))
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID")
REPORT_DAYS_BACK   = int(os.getenv("REPORT_DAYS_BACK", "1"))
TG_PARSE_MODE      = os.getenv("TG_PARSE_MODE", "Markdown")  # ou "MarkdownV2"

TG_MAX_LEN = 4000
TG_SAFETY  = 200
TG_CHUNK   = TG_MAX_LEN - TG_SAFETY

def log(msg: str):
    print(f"[{datetime.utcnow().isoformat()}Z] {msg}")

def escape_mdv2(text: str) -> str:
    if TG_PARSE_MODE != "MarkdownV2":
        return text
    for ch in r"_*[]()~`>#+-=|{}.!":
        text = text.replace(ch, f"\\{ch}")
    return text

def tg_send(text: str) -> None:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        log("Telegram não configurado; printando texto:")
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
    if len(text) <= TG_CHUNK:
        tg_send(text)
        return
    parts = [text[i:i+TG_CHUNK] for i in range(0, len(text), TG_CHUNK)]
    for idx, part in enumerate(parts, 1):
        tg_send(f"{part}\n\n({idx}/{len(parts)})")
        time.sleep(0.8)

Row = Dict[str, Any]

def load_rows_for_day(day_utc) -> List[Row]:
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
                try:
                    r["_price"] = float(r.get("price_total", "nan"))
                except Exception:
                    continue
                rows.append(r)
    return rows

def summarize_by_route(rows: List[Row]) -> Dict[str, Dict[str, Any]]:
    buckets: Dict[str, List[Row]] = defaultdict(list)
    for r in rows:
        ori, dest = r.get("origem"), r.get("destino")
        if not ori or not dest:
            continue
        buckets[f"{ori}-{dest}"].append(r)

    out: Dict[str, Dict[str, Any]] = {}
    for key, lst in buckets.items():
        best = min(lst, key=lambda rr: rr["_price"])
        avg  = sum(rr["_price"] for rr in lst) / max(1, len(lst))
        out[key] = {"best": best, "avg": avg, "count": len(lst)}
    return out

def google_flights_link(ori: str, dest: str, date_yyyy_mm_dd: str) -> str:
    return ("https://www.google.com/travel/flights?"
            f"hl=pt-BR&curr=BRL&flt={ori}.{dest}.{date_yyyy_mm_dd};tt=o")

def format_route_line(i: int, key: str, info: Dict[str, Any]) -> str:
    best = info["best"]
    ori, dest = best.get("origem", "???"), best.get("destino", "???")
    price = best.get("_price", 0.0)
    curr = best.get("currency", "BRL")
    dep  = best.get("departure_date") or ""
    airline = best.get("airline", "") or "N/A"
    avg = info["avg"]
    count = info["count"]
    link = google_flights_link(ori, dest, dep) if dep and ori and dest else ""

    if TG_PARSE_MODE == "MarkdownV2":
        title = escape_mdv2(f"{i:02d}. {ori}→{dest}  {price:.2f} {curr}")
        rest  = escape_mdv2(f"• Voo: {dep}  • CIA: {airline}  • Média: {avg:.2f}  (n={count})")
        if link:
            return f"{title}\n{rest}\n{escape_mdv2(link)}"
        return f"{title}\n{rest}"

    title = f"*{i:02d}. {ori}→{dest}*  `{price:.2f} {curr}`"
    rest  = f"• Voo: `{dep}`  • CIA: `{airline}`  • Média: `{avg:.2f}`  (n={count})"
    return f"{title}\n{rest}\n{link}" if link else f"{title}\n{rest}"

def build_report_text() -> str:
    today = datetime.utcnow().date()
    target_day = today - timedelta(days=REPORT_DAYS_BACK)
    rows = load_rows_for_day(target_day)
    if not rows:
        return f"📊 *Relatório de Preços*\n\nNenhum dado encontrado para {target_day:%d/%m/%Y}."

    summary = summarize_by_route(rows)
    ranking = sorted(summary.items(), key=lambda kv: kv[1]["best"]["_price"])

    header = (
        f"📊 *Relatório de Prêmios de Passagens*\n"
        f"🗓️ Dia (UTC): *{target_day:%d/%m/%Y}*\n"
        f"Rotas analisadas: *{len(ranking)}*\n\n"
    )
    lines: List[str] = [header]
    for i, (key, info) in enumerate(ranking, 1):
        lines.append(format_route_line(i, key, info))

    menor = ranking[0][1]["best"]["_price"]
    top_n = min(10, len(ranking))
    media_top = sum(ranking[j][1]["best"]["_price"] for j in range(top_n)) / top_n
    lines.append("\n—\n" + f"Menor do dia: *{menor:.2f}*\nMédia das TOP {top_n}: *{media_top:.2f}*\n")
    return "\n".join(lines)

def main() -> None:
    try:
        text = build_report_text()
        tg_send_chunked(text)
        print(text)
    except Exception as e:
        log(f"Erro ao gerar/enviar relatório: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()