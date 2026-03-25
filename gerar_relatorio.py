#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import requests
from datetime import datetime, timedelta
from collections import defaultdict
import psycopg2
from psycopg2.extras import DictCursor

from database import logger, get_connection

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID")
TG_PARSE_MODE      = os.getenv("TG_PARSE_MODE", "HTML") 

def tg_send(text: str) -> None:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logger.warning("Telegram não configurado.")
        return
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": TG_PARSE_MODE, "disable_web_page_preview": True},
            timeout=20,
        )
    except Exception as e:
        logger.error(f"Erro Telegram: {e}")

def read_rows_for(date_utc):
    """Lê do Supabase as entradas que começam com a data de ontem."""
    conn = get_connection()
    if not conn: return []
    
    date_str = date_utc.strftime('%Y-%m-%d')
    
    try:
        with conn.cursor(cursor_factory=DictCursor) as cursor:
            cursor.execute("""
                SELECT origem, destino, data, preco 
                FROM historico 
                WHERE ts LIKE %s
            """, (f"{date_str}%",))
            return [dict(row) for row in cursor.fetchall()]
    except Exception as e:
        logger.error(f"Erro DB: {e}")
        return []
    finally:
        conn.close()

def build_report(rows):
    if not rows:
        return "📊 Relatório diário: sem dados rastreados para ontem."

    best = defaultdict(lambda: {"total": float("inf"), "data_voo": ""})

    for r in rows:
        rota = f"{r['origem']}→{r['destino']}"
        tot = float(r["preco"])
        if tot < best[rota]["total"]:
            best[rota] = {"total": tot, "data_voo": r["data"]}

    ref = (datetime.utcnow().date() - timedelta(days=1)).strftime('%d/%m/%Y')
    lines = [f"📊 <b>Relatório Diário de Preços</b>\n🗓️ Referência: {ref}\n"]
    
    for rota, info in sorted(best.items()):
        lines.append(f"✈️ <b>{rota}</b>")
        lines.append(f"• Menor Preço: R$ {info['total']:.2f}")
        lines.append(f"• Data do Voo: {info['data_voo']}\n")

    return "\n".join(lines).strip()

def main():
    logger.info("A iniciar relatório...")
    ontem = datetime.utcnow().date() - timedelta(days=1)
    rows = read_rows_for(ontem)
    msg = build_report(rows)
    tg_send(msg)
    logger.info("Concluído.")

if __name__ == "__main__":
    main()
