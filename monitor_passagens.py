import os
import json
import random
import requests
import logging
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv
from serpapi import GoogleSearch
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

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

# ====================== PLAYWRIGHT MAXMILHAS ======================
def buscar_maxmilhas_playwright(origem: str, destino: str, ida: str, volta: str):
    """Scraper oficial com Playwright (headless Chromium)."""
    url = (
        f"https://www.maxmilhas.com.br/passagens-aereas"
        f"?from={origem}&to={destino}&departure={ida}"
        f"&return={volta}&adults=1&children=0&infants=0"
        f"&type=roundtrip"
    )
    
    voos = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.set_viewport_size({"width": 1280, "height": 800})
            
            logging.info(f"🌐 MaxMilhas → abrindo {origem}-{destino}")
            page.goto(url, wait_until="networkidle", timeout=45000)
            
            # Espera os resultados carregarem
            page.wait_for_selector('div[class*="flight-card"], div[class*="result-card"], [data-testid*="flight"]', timeout=30000)
            
            # Extrai os cards
            cards = page.query_selector_all('div[class*="flight-card"], div[class*="result-card"], [data-testid*="flight"]')
            
            for card in cards[:3]:  # Analisa os 3 primeiros
                try:
                    preco_el = card.query_selector('text=/R\\$\\s*[0-9.]+/')
                    if not preco_el:
                        preco_el = card.query_selector('span[class*="price"], div[class*="price"]')
                    preco_text = preco_el.inner_text().strip() if preco_el else "0"
                    preco = float(''.join(filter(str.isdigit, preco_text.replace(',', '.'))))
                    
                    if preco < 100:
                        continue
                        
                    voos.append({
                        "preco": round(preco, 2),
                        "link": url, # Link seguro e limpo
                        "fonte": "MaxMilhas"
                    })
                except Exception:
                    continue
            
            browser.close()
            logging.info(f"✅ MaxMilhas retornou {len(voos)} voos")
            return voos
            
    except PlaywrightTimeout:
        logging.warning("⏳ Timeout MaxMilhas - página demorou a carregar")
    except Exception as e:
        logging.error(f"❌ Playwright falhou: {e}")
    return []

# ====================== FUNÇÕES DE INFRAESTRUTURA ======================
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
    hoje = datetime.now(timezone.utc)
    dias_para_frente = random.randint(15, 120)
    data_alvo = hoje + timedelta(days=dias_para_frente)
    
    dias_para_sexta = (4 - data_alvo.weekday() + 7) % 7
    sexta = data_alvo + timedelta(days=dias_para_sexta)
    domingo = sexta + timedelta(days=2)
    return sexta.strftime('%Y-%m-%d'), domingo.strftime('%Y-%m-%d')

def buscar_hotel(destino_nome: str, check_in: str, check_out: str) -> dict | None:
    try:
        params = {
            "engine": "google_hotels", "q": f"Hotéis em {destino_nome}",
            "check_in_date": check_in, "check_out_date": check_out,
            "currency": "BRL", "hl": "pt", "gl": "br", "api_key": SERPAPI_KEY,
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
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": mensagem, "parse_mode": "Markdown", "disable_web_page_preview": True}
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        logging.error(f"Erro ao enviar Telegram: {e}")

# ====================== FUNÇÃO PRINCIPAL ======================
def buscar_passagens():
    logging.info("═══ Radar 5.2 (Google Flights + MaxMilhas) ═══")
    
    init_db()
    baselines = carregar_baselines()

    origem = random.choice(ORIGENS)
    destinos_validos = [d for d in DESTINOS if d["iata"] != origem["iata"]]
    destino = random.choice(destinos_validos)
    ida, volta = gerar_janela_aleatoria()

    logging.info(f"🔎 Analisando: {origem['iata']} → {destino['iata']}  [{ida} → {volta}]")

    # 1. Busca no Google Flights
    params = {
        "engine": "google_flights", "departure_id": origem["iata"], "arrival_id": destino["iata"],
        "outbound_date": ida, "return_date": volta, "currency": "BRL", "hl": "pt", "api_key": SERPAPI_KEY,
    }
    
    preco_google = None
    link_google = f"https://www.google.com/travel/flights?q=Flights%20to%20{destino['iata']}%20from%20{origem['iata']}%20on%20{ida}%20through%20{volta}"
    
    try:
        search = GoogleSearch(params)
        voos_google = search.get_dict().get("best_flights", [])
        if voos_google:
            preco_google = float(voos_google[0].get("price"))
    except Exception as e:
        logging.error(f"Erro no Google Flights: {e}")

    # 2. Busca na MaxMilhas
    voos_max = buscar_maxmilhas_playwright(origem["iata"], destino["iata"], ida, volta)
    preco_max = voos_max[0]["preco"] if voos_max else None
    link_max = voos_max[0]["link"] if voos_max else None

    # 3. Competição: Quem tem o menor preço?
    if preco_google and preco_max:
        preco_final = min(preco_google, preco_max)
        fonte_vencedora = "MaxMilhas" if preco_final == preco_max else "Google Flights"
        link_final = link_max if fonte_vencedora == "MaxMilhas" else link_google
    elif preco_max:
        preco_final, fonte_vencedora, link_final = preco_max, "MaxMilhas", link_max
    elif preco_google:
        preco_final, fonte_vencedora, link_final = preco_google, "Google Flights", link_google
    else:
        logging.info("❌ Nenhum voo encontrado em ambas as plataformas.")
        return

    # Salva no Histórico
    salvar_historico_db({
        "ts": datetime.now(timezone.utc).isoformat(),
        "origem": origem["iata"], "destino": destino["iata"],
        "data": ida, "preco": preco_final
    })

    # Estatística e Baseline
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
        
        if estatisticas.get("p10") and preco_final <= estatisticas["p10"]:
            status_promo = "🔥🔥 BOMBÁSTICA (Top 10% mais baratos)"
        elif preco_final <= teto_alerta:
            status_promo = "⭐ EXCELENTE (Top 25% mais baratos)"
    
    # Envio do Telegram
    if preco_final <= teto_alerta:
        logging.info(f"✅ Promocão! R${preco_final} via {fonte_vencedora}. Gerando alerta...")
        dica_ia = analisar_oferta_com_ia(origem["nome"], destino["nome"], preco_final)
        hotel = buscar_hotel(destino["nome"], ida, volta)
        
        bloco_hotel = ""
        if hotel:
            bloco_hotel = f"\n🏨 *Hospedagem:* {hotel['nome']} (Nota: {hotel['nota']})\n   💵 R$ {hotel['preco_total']:.2f} (Total FDS)\n   📦 *PACOTE:* R$ {preco_final + hotel['preco_total']:.2f}\n"
            if hotel["link"]: bloco_hotel += f"   🔗 [Ver Hotel]({hotel['link']})\n"

        msg = (f"{status_promo}\n\n🛫 *Rota:* {origem['nome']} → {destino['nome']}\n📅 *FDS:* {ida} a {volta}\n💰 *Voo:* R$ {preco_final:.2f} (Teto: R${teto_alerta:.2f})\n🏆 *Achado no:* {fonte_vencedora}\n{bloco_hotel}\n🤖 *Dica:* {dica_ia}\n\n✈️ [RESERVAR VOO]({link_final})")
        enviar_telegram(msg)
    else:
        logging.info(f"❌ Voo caro (R${preco_final} vs Teto R${teto_alerta}). Apenas salvo no histórico.")

if __name__ == "__main__":
    buscar_passagens()
