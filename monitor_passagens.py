import os
import requests
import json
import logging
from datetime import datetime, timedelta
from dotenv import load_dotenv
from serpapi import GoogleSearch

# CORREÇÃO: Importa a sua função 'gerar_dica_turismo' com o apelido que o monitor espera
from gemini_agent import gerar_dica_turismo as analisar_oferta_com_ia

# 1. Configurações Iniciais
load_dotenv()
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')

# Chaves de Ambiente (Pegas do GitHub Secrets ou .env)
SERPAPI_KEY = os.getenv("SERPAPI_KEY")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
ORIGEM = "GYN"

# 2. Configuração de Destinos e Tetos (Onde você quer ir?)
DESTINOS = [
    {"iata": "GIG", "nome": "Rio de Janeiro", "teto": 550.0},
    {"iata": "SSA", "nome": "Salvador", "teto": 850.0},
    {"iata": "MCZ", "nome": "Maceió", "teto": 1200.0},
    {"iata": "FLN", "nome": "Florianópolis", "teto": 700.0},
    {"iata": "FOR", "nome": "Fortaleza", "teto": 1050.0}
]

def proximos_finais_de_semana(quantidade=3):
    """Calcula as datas de ida (sexta) e volta (domingo) dos próximos finais de semana."""
    finais = []
    hoje = datetime.now()
    
    # Encontra a próxima sexta-feira útil
    dias_para_sexta = (4 - hoje.weekday() + 7) % 7
    if dias_para_sexta == 0: dias_para_sexta = 7 
    
    proxima_sexta = hoje + timedelta(days=dias_para_sexta)
    
    for i in range(quantidade):
        ida = proxima_sexta + timedelta(weeks=i)
        volta = ida + timedelta(days=2) # Domingo
        finais.append((ida.strftime('%Y-%m-%d'), volta.strftime('%Y-%m-%d')))
    
    return finais

def enviar_telegram(mensagem):
    """Envia o alerta formatado para o seu Telegram."""
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": mensagem, "parse_mode": "Markdown"}
    try:
        r = requests.post(url, json=payload, timeout=10)
        logging.info(f"Status Telegram: {r.status_code}")
    except Exception as e:
        logging.error(f"Erro ao enviar Telegram: {e}")

def buscar_passagens():
    logging.info("--- Radar GYN 2.0: Iniciando Busca de Finais de Semana ---")
    janelas_de_viagem = proximos_finais_de_semana(3) 
    
    for destino in DESTINOS:
        for ida, volta in janelas_de_viagem:
            logging.info(f"Buscando: {ORIGEM} -> {destino['iata']} ({ida} a {volta})")
            
            params = {
                "engine": "google_flights",
                "departure_id": ORIGEM,
                "arrival_id": destino['iata'],
                "outbound_date": ida,
                "return_date": volta,
                "currency": "BRL",
                "hl": "pt",
                "api_key": SERPAPI_KEY
            }

            try:
                search = GoogleSearch(params)
                results = search.get_dict()
                voos = results.get("best_flights", [])

                if not voos:
                    continue

                melhor_voo = voos[0]
                preco = melhor_voo.get("price")

                # Validação de Preço (Apenas se estiver abaixo do teto)
                if preco and preco <= destino['teto']:
                    # 3. Chama a sua função de IA com os parâmetros corretos
                    # Nota: Passamos ORIGEM e DESTINO conforme sua função pede
                    dica_ia = analisar_oferta_com_ia(ORIGEM, destino['nome'], preco)
                    
                    # Link amigável para o Google Flights
                    link = f"https://www.google.com/travel/flights?q=Flights%20to%20{destino['iata']}%20from%20{ORIGEM}%20on%20{ida}%20through%20{volta}"
                    
                    msg = (
                        f"✈️ *PASSAGEM ENCONTRADA!*\n\n"
                        f"📍 *Rota:* {ORIGEM} ➔ {destino['nome']}\n"
                        f"📅 *Ida:* {ida} (Sexta)\n"
                        f"📅 *Volta:* {volta} (Domingo)\n"
                        f"💰 *Preço:* R$ {preco:.2f}\n\n"
                        f"🤖 *Dica da IA:* {dica_ia}\n\n"
                        f"🔗 [Clique aqui para reservar]({link})"
                    )
                    
                    enviar_telegram(msg)
                    logging.info(f"✅ Sucesso! Alerta enviado para {destino['nome']}")

            except Exception as e:
                logging.error(f"Erro na busca para {destino['iata']}: {e}")

if __name__ == "__main__":
    buscar_passagens()
    logging.info("--- Monitor Finalizado ---")