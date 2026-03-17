#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import os
import sys
import json
import time
import random
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from dotenv import load_dotenv
from serpapi import GoogleSearch

# Importações dos nossos módulos personalizados
from database import logger, init_db, salvar_historico_db
from gemini_agent import gerar_dica_turismo

# Carrega as variáveis do ficheiro .env
load_dotenv()

# ==========================================================
# CONFIGURAÇÃO
# ==========================================================

class Config:
    ORIGEM = os.getenv("ORIGEM", "GYN").strip().upper()
    
    # Destinos padrão caso não estejam no .env
    DESTINOS_RAW = os.getenv("DESTINOS", "SSA,REC,FOR,MCZ,NAT,GIG,SDU,BSB,FLN,POA,CWB")
    DESTINOS = [d.strip().upper() for d in DESTINOS_RAW.split(",") if d.strip()]

    MAX_WORKERS = int(os.getenv("MAX_WORKERS", "3")) # Reduzido para poupar cota da API
    BASELINE_PATH = Path("data/baselines.json")
    
    DESTINOS_POR_EXEC = int(os.getenv("DESTINOS_POR_EXEC", "3"))
    SERPAPI_KEY = os.getenv("SERPAPI_KEY")
    
    CURRENCY = "BRL"

# ==========================================================
# REGRAS DE NEGÓCIO
# ==========================================================

MAX_PRECO_PP = float(os.getenv("MAX_PRECO_PP", "550.0"))
MIN_DISCOUNT_PCT = float(os.getenv("MIN_DISCOUNT_PCT", "0.15"))

# ==========================================================
# MOTOR DE BUSCA (GOOGLE FLIGHTS VIA SERPAPI)
# ==========================================================

def buscar_voo_google(origem: str, destino: str, data: str) -> Optional[Dict]:
    """Procura voos reais no Google Flights usando a SerpApi."""
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
        "api_key": Config.SERPAPI_KEY
    }

    try:
        search = GoogleSearch(params)
        results = search.get_dict()
        
        # Filtra pela melhor opção (Best Flights) do Google
        if "best_flights" in results and results["best_flights"]:
            best = results["best_flights"][0]
            return {
                "preco": float(best["price"]),
                "companhia": best["flights"][0]["airline"],
                "link": results.get("search_metadata", {}).get("google_flights_url")
            }
        
        # Se não houver 'best', tenta 'other_flights'
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
    # Gera uma data aleatória entre 15 e 60 dias para busca
    hoje = datetime.now()
    data_str = (hoje + timedelta(days=random.randint(15, 60))).strftime("%Y-%m-%d")

    logger.info(f"Buscando: {Config.ORIGEM} -> {dest} para {data_str}")
    
    resultado = buscar_voo_google(Config.ORIGEM, dest, data_str)
    
    if not resultado:
        logger.warning(f"Nenhum voo encontrado para {dest}")
        return

    preco = resultado["preco"]
    companhia = resultado["companhia"]
    link = resultado["link"]

    # Verifica se o preço é um alerta (abaixo do teto definido)
    if preco <= MAX_PRECO_PP:
        # IA gera uma dica persuasiva
        dica_ia = gerar_dica_turismo(Config.ORIGEM, dest, preco)
        bloco_ia = f"\n🤖 <b>Dica da IA:</b>\n<i>{dica_ia}</i>\n" if dica_ia else ""

        msg = f"""
✈️ <b>PASSAGEM ENCONTRADA (REAL)</b>

📍 <b>Rota:</b> {Config.ORIGEM} → {dest}
📅 <b>Data:</b> {data_str}
🏢 <b>Cia:</b> {companhia}

💰 <b>Preço:</b> R$ {preco:.2f}
{bloco_ia}
🔎 <b>Link direto Google Flights:</b>
{link}
"""
        tg_send(msg)

    # Grava na Base de Dados
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
    logger.info("--- Monitor FlightHunter (Google Search Engine) Iniciado ---")

    # Seleciona uma amostra de destinos para não estourar a cota gratuita da SerpApi
    destinos_amostra = random.sample(Config.DESTINOS, min(len(Config.DESTINOS), Config.DESTINOS_POR_EXEC))

    with ThreadPoolExecutor(max_workers=Config.MAX_WORKERS) as executor:
        executor.map(process_dest, destinos_amostra)

    logger.info("--- Monitor Finalizado ---")

if __name__ == "__main__":
    main()