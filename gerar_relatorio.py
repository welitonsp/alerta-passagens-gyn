#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import csv
from datetime import datetime, timedelta
from collections import defaultdict
import requests
from pathlib import Path

HISTORY_PATH = Path(os.getenv("HISTORY_PATH", "data/history.csv"))
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
TG_PARSE_MODE = os.getenv("TG_PARSE_MODE", "HTML")  # simples

def log(msg): print(f"[{datetime.utcnow().isoformat()}Z] {msg}")

def tg_send(text: str):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        log("Telegram não configurado. Pulando envio.")
        return
    requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
        json={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": TG_PARSE_MODE, "disable_web_page_preview": True},
        timeout=20,
    )

def read_yesterday_rows():
    if not HISTORY_PATH.exists():
        log("Sem histórico ainda.")
        return []

    yesterday = (datetime.utcnow().date() - timedelta(days=1))
    out = []
    with HISTORY_PATH.open("r", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            try:
                ts = datetime.fromisoformat(row["ts_utc"].replace("Z", "+00:00"))
                if ts.date() == yesterday:
                    out.append(row)
            except Exception:
                continue
    return out

def build_report(rows):
    if not rows:
        return "📊 Relatório diário: sem dados para ontem."

    best = defaultdict(lambda: {"price": float("inf"), "currency": "BRL", "date": "", "airline": ""})
    for r in rows:
        key = f"{r['origem']}→{r['destino']}"
        price = float(r["price_total"])
        if price < best[key]["price"]:
            best[key] = {
                "price": price,
                "currency": r.get("currency","BRL"),
                "date": r.get("departure_date",""),
                "airline": r.get("airline",""),
            }

    lines = [f"📊 <b>Relatório de Preços</b>", f"🗓️ Referência: {(datetime.utcnow().date() - timedelta(days=1)).strftime('%d/%m/%Y')}", ""]
    for rota, info in sorted(best.items()):
        lines.append(f"✈️ <b>{rota}</b>")
        lines.append(f"• Preço: {info['price']:.2f} {info['currency']}")
        if info["date"]:
            lines.append(f"• Voo: {info['date']} ({info['airline'] or 'N/A'})")
        lines.append("")

    return "\n".join(lines)

def main():
    rows = read_yesterday_rows()
    text = build_report(rows)
    tg_send(text)
    log("Relatório enviado.")

if __name__ == "__main__":
    main()