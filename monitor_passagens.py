import os
import json
import random
import requests
import logging
from datetime import datetime, timedelta, timezone # <-- Adicionado timezone
from dotenv import load_dotenv
from serpapi import GoogleSearch

from gemini_agent import gerar_dica_turismo as analisar_oferta_com_ia
from database import init_db, salvar_historico_db, DATA_DIR

load_dotenv()
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')

SERPAPI_KEY       = os.getenv("SERPAPI_KEY")
TELEGRAM_TOKEN    = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID  = os.getenv("TELEGRAM_CHAT_ID")

ORIGENS = [
    {"iata": "GYN", "nome": "Goiânia"},
    {"iata": "CLV", "nome": "Caldas Novas"}
]

# Todas as 27 capitais do Brasil
DESTINOS = [
    {"iata": "RBR", "nome": "Rio Branco"}, {"iata": "MCZ", "nome": "Maceió"},
    {"iata": "MCP", "nome": "Macapá"}, {"iata": "MAO", "nome": "Manaus"},
    {"iata": "SSA", "nome": "Salvador"}, {"iata": "FOR", "nome": "Fortaleza"},
    {"iata": "BSB", "nome": "Brasília"}, {"iata": "VIX", "nome": "Vitória"},
    {"iata": "SLZ", "nome": "São Luís"}, {"iata": "CGB", "nome": "Cuiabá"},
    {"iata": "CGR", "nome": "Campo Grande"}, {"iata": "CNF", "nome": "Belo Horizonte"},
    {"iata": "BEL", "nome": "Belém"}, {"iata": "JPA", "nome": "João Pessoa"},
    {"iata": "CWB", "nome": "Curitiba"}, {"iata": "REC", "nome": "Recife"},
    {"iata": "THE", "nome": "Teresina"}, {"iata": "GIG", "nome": "Rio de Janeiro"},
    {"iata": "NAT", "nome": "Natal"}, {"iata": "POA", "nome": "Porto Alegre"},
    {"iata": "PVH", "nome": "Porto Velho"}, {"iata": "BVB", "nome": "Boa Vista"},
    {"iata": "FLN", "nome": "Florianópolis"}, {"iata": "SAO", "nome": "São Paulo"},
    {"iata": "AJU", "nome": "Aracaju"}, {"iata": "PMW", "nome": "Palmas"}
]

def carregar_baselines():
    caminho = DATA_DIR / "baselines.json"
    if caminho.exists():
        try:
            with open(caminho, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logging.warning(f"Aviso - Não foi possível ler baselines.json: {e}")
    return {}

def calcular_bucket(dias_antecedencia: int) -> str:
    if dias_antecedencia <= 6: return "0-6"
    if dias_antecedencia <= 13: return "7-13"
    if dias_antecedencia <= 20: return "14-20"
    if dias_antecedencia <= 27: return "21-27"
    if dias_antecedencia <= 34: return "28-34"
    if dias_antecedencia <= 49: return "35-49"
    if dias_antecedencia <= 69: return "50-69"
    return "70-90"

def gerar_janela_aleatoria():
    """Gera UM único final de semana aleatório entre 15 e 120 dias no futuro"""
    hoje = datetime.now(timezone.utc)
    # Sorteia quantos dias no futuro vamos procurar
    dias_para_frente = random.randint(15, 120)
    data_alvo = hoje + timedelta(days=dias_para_frente)
    
    # Encontra a sexta-feira mais próxima dessa data
    dias_para_sexta = (4 - data_alvo.weekday() + 7) % 7
    sexta = data_alvo + timedelta(days=dias_para_sexta)
    domingo = sexta + timedelta(days=2)
    
    return sexta.strftime('%Y-%m-%d'), domingo.strftime('%Y-%m-%d')

def buscar_hotel(destino_nome: str, check_in: str, check_out: str) -> dict | None:
    try:
        params = {
            "engine": "google_hotels",
            "q": f"Hotéis em {destino_nome}",
            "check_in_date": check_in,
            "check_out_date": check_out,
            "currency": "BRL", "hl": "pt", "gl": "br",
            "api_key": SERPAPI_KEY,
        }
        search = GoogleSearch(params)
        hoteis = search.get_dict().get("properties", [])
        
        if not hoteis: return None
        hoteis_com_preco = [h for h in hoteis if h.get("total_rate", {}).get("extracted_lowest")]
        if not hoteis_com_preco: return None

        hoteis_com_preco.sort(key=lambda h: h["total_rate"]["extracted_lowest"])
        melhor = hoteis_com_preco[0]

        return {
            "nome": melhor.get("name", "Hotel"),
            "nota": melhor.get("overall_rating", "N/A"),
            "preco_total": melhor["total_rate"]["extracted_lowest"],
            "link": melhor.get("link", "")
        }
    except Exception as e:
        return None

def enviar_telegram(mensagem: str):
    # Proteção sugerida pelo revisor
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        logging.warning("Credenciais do Telegram ausentes. Alerta não enviado.")
        return
        
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID, 
        "text": mensagem, 
        "parse_mode": "Markdown", 
        "disable_web_page_preview": True
    }
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        logging.error(f"Erro ao enviar Telegram: {e}")

def buscar_passagens():
    logging.info("═══ Radar 5.1 (IA de Preços + Datas Aleatórias) ═══")
    
    init_db()
    baselines = carregar_baselines()

    origem = random.choice(ORIGENS)
    destinos_validos = [d for d in DESTINOS if d["iata"] != origem["iata"]]
    destino = random.choice(destinos_validos)
    
    # Nova lógica de datas
    ida, volta = gerar_janela_aleatoria()

    logging.info(f"🔎 Sorteio: {origem['iata']} → {destino['iata']}  [{ida} → {volta}]")

    params = {
        "engine": "google_flights", "departure_id": origem["iata"], "arrival_id": destino["iata"],
        "outbound_date": ida, "return_date": volta, "currency": "BRL", "hl": "pt", "api_key": SERPAPI_KEY,
    }

    try:
        search = GoogleSearch(params)
        voos = search.get_dict().get("best_flights", [])

        if not voos:
            logging.info("Nenhum voo encontrado nesta rota/data.")
            return

        preco = float(voos[0].get("price"))

        # Fuso horário corrigido para UTC no histórico
        salvar_historico_db({
            "ts": datetime.now(timezone.utc).isoformat(),
            "origem": origem["iata"], "destino": destino["iata"],
            "data": ida, "preco": preco
        })

        # Fuso horário corrigido para cálculo da baseline
        data_voo_dt = datetime.strptime(ida, '%Y-%m-%d').date()
        hoje_dt = datetime.now(timezone.utc).date()
        dias_antecedencia = max(0, (data_voo_dt - hoje_dt).days)
        bucket_str = calcular_bucket(dias_antecedencia)
        
        chave_estatistica = f"{origem['iata']}-{destino['iata']}-{data_voo_dt.weekday()}-{bucket_str}"
        
        teto_alerta = 850.0 
        status_promo = "⚠️ Rota Nova (Aprendendo...)"

        if chave_estatistica in baselines:
            estatisticas = baselines[chave_estatistica]
            if estatisticas.get("p25"): teto_alerta = estatisticas["p25"]
            elif estatisticas.get("p50"): teto_alerta = estatisticas["p50"]
            
            if estatisticas.get("p10") and preco <= estatisticas["p10"]:
                status_promo = "🔥🔥 BOMBÁSTICA (Top 10% mais baratos da história)"
            elif preco <= teto_alerta:
                status_promo = "⭐ EXCELENTE (Top 25% mais baratos da história)"
        
        if preco <= teto_alerta:
            logging.info(f"✅ Preço Bateu a IA! (Atual: R${preco} | Teto: R${teto_alerta}). Enviando alerta...")
            dica_ia = analisar_oferta_com_ia(origem["nome"], destino["nome"], preco)
            hotel = buscar_hotel(destino["nome"], ida, volta)
            
            bloco_hotel = ""
            if hotel:
                bloco_hotel = f"\n🏨 *Hospedagem:* {hotel['nome']} (Nota: {hotel['nota']})\n   💵 R$ {hotel['preco_total']:.2f} (Total do FDS)\n   📦 *PACOTE:* R$ {preco + hotel['preco_total']:.2f}\n"
                if hotel["link"]: bloco_hotel += f"   🔗 [Ver Hotel]({hotel['link']})\n"

            msg = (f"{status_promo}\n\n🛫 *Rota:* {origem['nome']} → {destino['nome']}\n📅 *FDS:* {ida} a {volta}\n💰 *Voo:* R$ {preco:.2f} (Teto: R${teto_alerta:.2f})\n{bloco_hotel}\n🤖 *Dica IA:* {dica_ia}\n\n✈️ [RESERVAR VOO](https://www.google.com/travel/flights?q=Flights%20to%20{destino['iata']}%20from%20{origem['iata']}%20on%20{ida}%20through%20{volta})")
            enviar_telegram(msg)
        else:
            logging.info(f"❌ Voo caro (R${preco} vs Teto R${teto_alerta}). Histórico salvo.")

    except Exception as e:
        logging.error(f"Erro na busca: {e}")

if __name__ == "__main__":
    buscar_passagens()
