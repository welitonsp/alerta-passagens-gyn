#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import csv
import requests
from pathlib import Path
from datetime import datetime, timedelta
from collections import defaultdict

# ===== Config =====
HISTORY_PATH = Path(os.getenv("HISTORY_PATH", "data/history.csv"))
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID")
TG_PARSE_MODE      = os.getenv("TG_PARSE_MODE", "HTML")  # HTML é simples e seguro

def log(msg: str) -> None:
    print(f"[{datetime.utcnow().isoformat()}Z] {msg}")

def tg_send(text: str) -> None:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        log("Telegram não configurado. Pulando envio.")
        return
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={
                "chat_id": int(TELEGRAM_CHAT_ID),
                "text": text,
                "parse_mode": TG_PARSE_MODE,
                "disable_web_page_preview": True
            },
            timeout=20,
        )
        log(f"Telegram HTTP {r.status_code}")
    except Exception as e:
        log(f"Erro ao enviar Telegram: {e}")

def read_rows_for(date_utc):
    """Lê apenas as linhas do CSV cujo ts_utc seja na 'date_utc' (UTC)."""
    if not HISTORY_PATH.exists():
        log("Sem histórico ainda.")
        return []

    rows = []
    with HISTORY_PATH.open("r", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            try:
                ts = datetime.fromisoformat(row["ts_utc"].replace("Z", "+00:00"))
                if ts.date() == date_utc:
                    rows.append(row)
            except Exception:
                continue
    return rows

def _to_float(v, default=0.0):
    try:
        return float(v)
    except Exception:
        return default

def build_report(rows):
    """Monta relatório diário com o MENOR total ida+volta por rota (origem→destino)."""
    if not rows:
        return "📊 Relatório diário: sem dados para ontem."

    # Estrutura: guarda o melhor total por rota + breakdown
    # Compatível com CSV novo (ida+volta) e faz fallback caso exista CSV antigo.
    best = defaultdict(lambda: {
        "total": float("inf"),
        "cur": "BRL",
        "d_ida": "", "d_volta": "",
        "p_ida": 0.0, "p_volta": 0.0,
        "cia_ida": "", "cia_volta": ""
    })

    for r in rows:
        rota = f"{r.get('origem','')}→{r.get('destino','')}"
        # CSV novo
        total = r.get("price_total")
        if total is not None:
            tot = _to_float(total, float("inf"))
            cur = r.get("currency", "BRL")
            d_ida = r.get("departure_date", "")
            d_volta = r.get("return_date", "")

            p_ida = _to_float(r.get("price_outbound", 0))
            p_volta = _to_float(r.get("price_inbound", 0))
            cia_ida = r.get("airline_outbound", "") or "N/A"
            cia_volta = r.get("airline_inbound", "") or "N/A"
        else:
            # Fallback CSV antigo (apenas ida)
            # Campos antigos: price_total (às vezes “price”), currency, departure_date, airline
            # Aqui tratamos como “ida” apenas, sem volta.
            tot = _to_float(r.get("price", r.get("price_total", float("inf"))), float("inf"))
            cur = r.get("currency", "BRL")
            d_ida = r.get("departure_date", r.get("date",""))
            d_volta = ""
            p_ida = tot
            p_volta = 0.0
            cia_ida = r.get("airline", "") or "N/A"
            cia_volta = "N/A"

        if tot < best[rota]["total"]:
            best[rota] = {
                "total": tot,
                "cur": cur,
                "d_ida": d_ida,
                "d_volta": d_volta,
                "p_ida": p_ida,
                "p_volta": p_volta,
                "cia_ida": cia_ida,
                "cia_volta": cia_volta
            }

    # Montagem da mensagem
    ref = (datetime.utcnow().date() - timedelta(days=1)).strftime('%d/%m/%Y')
    lines = [
        "📊 <b>Relatório de Preços (ida+volta)</b>",
        f"🗓️ Referência: {ref}",
        ""
    ]
    for rota, info in sorted(best.items()):
        lines.append(f"✈️ <b>{rota}</b>")
        lines.append(f"• Total: {info['total']:.2f} {info['cur']}")
        if info["d_ida"]:
            lines.append(f"• Ida {info['d_ida']}: {info['p_ida']:.2f} {info['cur']} ({info['cia_ida']})")
        if info["d_volta"]:
            lines.append(f"• Volta {info['d_volta']}: {info['p_volta']:.2f} {info['cur']} ({info['cia_volta']})")
        lines.append("")

    return "\n".join(lines).strip()

def main():
    ontem = datetime.utcnow().date() - timedelta(days=1)
    rows = read_rows_for(ontem)
    msg = build_report(rows)
    tg_send(msg)
    log("Relatório enviado.")

if __name__ == "__main__":
    main()