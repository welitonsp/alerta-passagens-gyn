#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sqlite3
import requests
from datetime import datetime, timedelta
from collections import defaultdict

# Importando as configurações unificadas do nosso novo módulo
from database import logger, DB_PATH

# ===== Config =====
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID")
TG_PARSE_MODE      = os.getenv("TG_PARSE_MODE", "HTML") 

def tg_send(text: str) -> None:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logger.warning("Telegram não configurado. Pulando envio do relatório.")
        return
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": text,
                "parse_mode": TG_PARSE_MODE,
                "disable_web_page_preview": True
            },
            timeout=20,
        )
        logger.info(f"Telegram HTTP {r.status_code}")
    except Exception as e:
        logger.error(f"Erro ao enviar relatório no Telegram: {e}")

def read_rows_for(date_utc):
    """Lê apenas as linhas do SQLite cuja data de registro (ts) seja 'date_utc'."""
    if not os.path.exists(DB_PATH):
        logger.warning("Banco de dados ainda não existe. Sem histórico para o relatório.")
        return []

    # Como o ts está em formato ISO (ex: 2026-03-17T10:55...Z)
    # Buscamos as entradas que começam com a data de ontem (YYYY-MM-DD)
    date_str = date_utc.strftime('%Y-%m-%d')
    
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row # Permite acessar as colunas pelo nome (como dict)
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT origem, destino, data, preco 
        FROM historico 
        WHERE ts LIKE ?
    """, (f"{date_str}%",))
    
    rows = [dict(row) for row in cursor.fetchall()]
    conn.close()
    
    return rows

def build_report(rows):
    """Monta relatório diário com o MENOR preço encontrado por rota (origem→destino)."""
    if not rows:
        return "📊 Relatório diário: sem dados rastreados para ontem."

    # Estrutura: guarda o melhor preço por rota
    best = defaultdict(lambda: {"total": float("inf"), "data_voo": ""})

    for r in rows:
        rota = f"{r['origem']}→{r['destino']}"
        tot = float(r["preco"])
        
        if tot < best[rota]["total"]:
            best[rota] = {
                "total": tot,
                "data_voo": r["data"]
            }

    # Montagem da mensagem
    ref = (datetime.utcnow().date() - timedelta(days=1)).strftime('%d/%m/%Y')
    lines = [
        "📊 <b>Relatório Diário de Preços</b>",
        f"🗓️ Referência das buscas: {ref}",
        ""
    ]
    
    for rota, info in sorted(best.items()):
        lines.append(f"✈️ <b>{rota}</b>")
        lines.append(f"• Menor Preço Encontrado: R$ {info['total']:.2f}")
        lines.append(f"• Data do Voo: {info['data_voo']}")
        lines.append("")

    return "\n".join(lines).strip()

def main():
    logger.info("Iniciando geração do relatório diário...")
    ontem = datetime.utcnow().date() - timedelta(days=1)
    
    rows = read_rows_for(ontem)
    msg = build_report(rows)
    
    tg_send(msg)
    logger.info("Geração de relatório concluída.")

if __name__ == "__main__":
    main()
