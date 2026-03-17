import os
import requests
import logging
from datetime import datetime, timedelta
from dotenv import load_dotenv
from serpapi import GoogleSearch

# Importa sua IA configurada
from gemini_agent import gerar_dica_turismo as analisar_oferta_com_ia

load_dotenv()
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')

SERPAPI_KEY       = os.getenv("SERPAPI_KEY")
TELEGRAM_TOKEN    = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID  = os.getenv("TELEGRAM_CHAT_ID")

# Origens
ORIGENS = [
    {"iata": "GYN", "nome": "Goiânia"},
    {"iata": "CLV", "nome": "Caldas Novas"}
]

# Destinos Enxutos (Para não estourar a API Gratuita de 100 buscas/mês)
DESTINOS = [
    {"iata": "GIG", "nome": "Rio de Janeiro", "teto": 580.0},
    {"iata": "SSA", "nome": "Salvador",       "teto": 880.0},
    {"iata": "MCZ", "nome": "Maceió",         "teto": 1150.0},
    {"iata": "FOR", "nome": "Fortaleza",      "teto": 1050.0}
]

def gerar_janelas_futuras():
    """Gera 3 finais de semana entre 3 e 6 meses à frente."""
    janelas = []
    hoje = datetime.now()
    data_inicio_busca = hoje + timedelta(days=90)

    for i in range(3):
        sexta = data_inicio_busca + timedelta(weeks=i * 4)
        dias_para_sexta = (4 - sexta.weekday() + 7) % 7
        sexta = sexta + timedelta(days=dias_para_sexta)
        domingo = sexta + timedelta(days=2)
        janelas.append((sexta.strftime('%Y-%m-%d'), domingo.strftime('%Y-%m-%d')))

    return janelas

def buscar_hotel(destino_nome: str, check_in: str, check_out: str) -> dict | None:
    """Busca o hotel mais barato no Google Hotels."""
    try:
        params = {
            "engine": "google_hotels",
            "q": f"Hotéis em {destino_nome}",
            "check_in_date": check_in,
            "check_out_date": check_out,
            "currency": "BRL",
            "hl": "pt",
            "gl": "br",
            "api_key": SERPAPI_KEY,
        }
        search = GoogleSearch(params)
        results = search.get_dict()
        hoteis = results.get("properties", [])
        
        if not hoteis:
            return None

        # Pega o mais barato com preço extraído
        hoteis_com_preco = [h for h in hoteis if h.get("total_rate", {}).get("extracted_lowest")]
        if not hoteis_com_preco:
            return None

        hoteis_com_preco.sort(key=lambda h: h["total_rate"]["extracted_lowest"])
        melhor = hoteis_com_preco[0]

        return {
            "nome": melhor.get("name", "Hotel"),
            "nota": melhor.get("overall_rating", "N/A"),
            "preco_total": melhor["total_rate"]["extracted_lowest"],
            "link": melhor.get("link", "")
        }
    except Exception as e:
        logging.warning(f"Aviso - Hotel não encontrado para {destino_nome}: {e}")
        return None

def enviar_telegram(mensagem: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": mensagem, "parse_mode": "Markdown", "disable_web_page_preview": True}
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        logging.error(f"Erro ao enviar Telegram: {e}")

def buscar_passagens():
    logging.info("═══ Radar 4.0 (Voo + Hotel) iniciado ═══")
    janelas = gerar_janelas_futuras()

    for origem in ORIGENS:
        for destino in DESTINOS:
            for ida, volta in janelas:
                logging.info(f"🔎 {origem['iata']} → {destino['iata']}  [{ida} → {volta}]")

                params = {
                    "engine": "google_flights",
                    "departure_id": origem["iata"],
                    "arrival_id": destino["iata"],
                    "outbound_date": ida,
                    "return_date": volta,
                    "currency": "BRL",
                    "hl": "pt",
                    "api_key": SERPAPI_KEY,
                }

                try:
                    search = GoogleSearch(params)
                    results = search.get_dict()
                    voos = results.get("best_flights", [])

                    if not voos: continue

                    melhor_voo = voos[0]
                    preco = melhor_voo.get("price")

                    if not preco or preco > destino["teto"]: continue

                    # 1. IA cria a dica de roteiro
                    dica_ia = analisar_oferta_com_ia(origem["nome"], destino["nome"], preco)

                    # 2. Busca de Hotel para formar o pacote
                    hotel = buscar_hotel(destino["nome"], ida, volta)
                    bloco_hotel = ""
                    
                    if hotel:
                        total_viagem = preco + hotel["preco_total"]
                        bloco_hotel = (
                            f"\n🏨 *Hospedagem Econômica:* {hotel['nome']} (Nota: {hotel['nota']})\n"
                            f"   💵 R$ {hotel['preco_total']:.2f} (Total do FDS)\n"
                            f"   📦 *PACOTE (Voo+Hotel):* R$ {total_viagem:.2f}\n"
                        )
                        if hotel["link"]:
                            bloco_hotel += f"   🔗 [Ver Hotel]({hotel['link']})\n"

                    link_voo = f"https://www.google.com/travel/flights?q=Flights%20to%20{destino['iata']}%20from%20{origem['iata']}%20on%20{ida}%20through%20{volta}"

                    msg = (
                        f"🌟 *PROMOÇÃO DETECTADA (3-6 Meses)*\n\n"
                        f"🛫 *Rota:* {origem['nome']} → {destino['nome']}\n"
                        f"📅 *FDS:* {ida} a {volta}\n"
                        f"💰 *Voo Ida e Volta:* R$ {preco:.2f}"
                        f"{bloco_hotel}\n"
                        f"🤖 *Consultor IA:* {dica_ia}\n\n"
                        f"✈️ [RESERVAR VOO]({link_voo})"
                    )

                    enviar_telegram(msg)
                    logging.info(f"✅ Alerta Pacote enviado: {origem['iata']}→{destino['iata']} R${preco:.2f}")

                except Exception as e:
                    logging.error(f"Erro na busca {origem['iata']}→{destino['iata']}: {e}")

    logging.info("═══ Radar 4.0 finalizado ═══")

if __name__ == "__main__":
    buscar_passagens()