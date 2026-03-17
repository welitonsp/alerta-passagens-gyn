#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import os
import random
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, Optional
from concurrent.futures import ThreadPoolExecutor

import requests
from dotenv import load_dotenv
from serpapi import GoogleSearch

# Importações dos nossos módulos personalizados
from database import logger, init_db, salvar_historico_db
from gemini_agent import gerar_dica_turismo

# Carrega as variáveis do ficheiro .env
load_dotenv()

# ==========================================================
# CONFIGURAÇÃO DE ESPECIALISTA
# ==========================================================

class Config:
    ORIGEM = os.getenv("ORIGEM", "GYN").strip().upper()
    
    # Destinos padrão caso não estejam no .env
    DESTINOS_RAW = os.getenv("DESTINOS", "SSA,REC,FOR,MCZ,NAT,GIG,SDU,BSB,FLN,POA,CWB")
    DESTINOS = [d.strip().upper() for d in DESTINOS_RAW.split(",") if d.strip()]

    MAX_WORKERS = int(os.getenv("MAX_WORKERS", "3"))
    DESTINOS_POR_EXEC = int(os.getenv("DESTINOS_POR_EXEC", "3"))
    SERPAPI_KEY = os.getenv("SERPAPI_KEY")
    CURRENCY = "BRL"

    # MODO DE TESTE: Se for True, ele envia a mensagem pro Telegram ignorando o preço.
    # No dia a dia, deixe como False.
    MODO_TESTE = True 

# ==========================================================
# REGRAS DE NEGÓCIO: TETOS DINÂMICOS POR DESTINO
# ==========================================================
# O robô agora sabe quanto vale a pena pagar para cada lugar saindo de GYN.
TETOS_POR_DESTINO = {
    "BSB": 250.0,  # Brasília é perto, passagem cara não compensa
    "CWB": 500.0,
    "FLN": 550.0,
    "SDU": 450.0,  # Rio de Janeiro
    "GIG": 450.0,
    "POA": 600.0,
    "SSA": 700.0,  # Nordeste começa a ficar mais caro
    "MCZ": 800.0,
    "REC": 850.0,
    "NAT": 900.0,
    "FOR": 950.0   # Fortaleza é mais longe, teto maior
}

# Preço de segurança caso você adicione um destino novo que não está na lista acima
TETO_PADRAO_FALLBACK = 600.0 

# ==========================================================
# MOTOR DE BUSCA (GOOGLE FLIGHTS VIA SERPAPI)
# ==========================================================

def buscar_voo_google(origem: str, destino: str, data: str) -> Optional[Dict]:
    """Procura voos reais no Google Flights usando a SerpApi (Apenas Ida)."""
    if not Config.SERPAPI_KEY:
        logger.error("SERPAPI_KEY não encontrada no ambiente ou .env")
        return None

    params = {
        "engine": "google_flights",
        "departure_id": origem,
        "arrival_id": destino,
        "outbound_date": data,
        "currency": Config.CURRENCY,
        "hl": "pt",
        "type": "2", # 2 = Apenas Ida
        "api_key": Config.SERPAPI_KEY
    }

    try:
        search = GoogleSearch(params)
        results = search.get_dict()
        
        if "best_flights" in results and results["best_flights"]:
            best = results["best_flights"][0]
            return {
                "preco": float(best["price"]),
                "companhia": best["flights"][0]["airline"],
                "link": results.get("search_metadata", {}).get("google_flights_url")
            }
        
        elif "other_flights" in results and results["other_flights"]:
            other = results["other_flights"][0]
            return {
                "preco": float(other["price"]),
                "companhia": other["flights"][0]["airline"],
                "link": results.get("search_metadata", {}).get("google_flights_url")
            }
            
    except Exception as e:
        logger.error(f"Erro na SerpApi para {origem}->{destino}: {e}")
    
    return None

# ==========================================================
# TELEGRAM
# ==========================================================

def tg_send(text: str):
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    
    if not token or not chat_id:
        return

    try:
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": False
            },
            timeout=20
        )
    except Exception as e:
        logger.error(f"Erro no Telegram: {e}")

# ==========================================================
# LÓGICA DE PROCESSAMENTO
# ==========================================================

def process_dest(dest: str):
    hoje = datetime.now()
    data_str = (hoje + timedelta(days=random.randint(15, 60))).strftime("%Y-%m-%d")

    # Descobre qual é o preço teto para este destino específico
    teto_maximo = TETOS_POR_DESTINO.get(dest, TETO_PADRAO_FALLBACK)

    logger.info(f"Buscando: {Config.ORIGEM} -> {dest} para {data_str} (Teto: R$ {teto_maximo})")
    
    resultado = buscar_voo_google(Config.ORIGEM, dest, data_str)
    
    if not resultado:
        logger.warning(f"Nenhum voo encontrado para {dest}")
        return

    preco = resultado["preco"]
    companhia = resultado["companhia"]
    link = resultado["link"]

    # Lógica Inteligente: Dispara se for barato OU se estivermos em modo de teste
    if preco <= teto_maximo or Config.MODO_TESTE:
        alerta_teste = "⚠️ [MODO DE TESTE ATIVADO]\n\n" if Config.MODO_TESTE else ""
        
        # IA gera a dica
        dica_ia = gerar_dica_turismo(Config.ORIGEM, dest, preco)
        bloco_ia = f"\n🤖 <b>Dica da IA:</b>\n<i>{dica_ia}</i>\n" if dica_ia else ""

        msg = f"""
{alerta_teste}✈️ <b>PASSAGEM ENCONTRADA (REAL)</b>

📍 <b>Rota:</b> {Config.ORIGEM} → {dest}
📅 <b>Data:</b> {data_str}
🏢 <b>Cia:</b> {companhia}

💰 <b>Preço:</b> R$ {preco:.2f} (Abaixo do teto de R$ {teto_maximo})
{bloco_ia}
🔎 <b>Link direto Google Flights:</b>
{link}
"""
        tg_send(msg)

    # Grava na Base de Dados sempre (para termos histórico e gerar gráficos depois)
    salvar_historico_db({
        "ts": datetime.now().isoformat(),
        "origem": Config.ORIGEM,
        "destino": dest,
        "data": data_str,
        "preco": preco
    })

# ==========================================================
# MAIN
# ==========================================================

def main():
    init_db()
    logger.info("--- Monitor FlightHunter (Smart Pricing) Iniciado ---")

    destinos_amostra = random.sample(Config.DESTINOS, min(len(Config.DESTINOS), Config.DESTINOS_POR_EXEC))

    with ThreadPoolExecutor(max_workers=Config.MAX_WORKERS) as executor:
        list(executor.map(process_dest, destinos_amostra))

    logger.info("--- Monitor Finalizado ---")

if __name__ == "__main__":
    main()