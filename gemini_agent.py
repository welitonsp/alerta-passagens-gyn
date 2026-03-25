import os
from google import genai
from dotenv import load_dotenv

load_dotenv()

CHAVE_API = os.getenv("GEMINI_API_KEY")

# Inicializa o cliente novo do Gemini
if CHAVE_API:
    client = genai.Client(api_key=CHAVE_API)
else:
    client = None

def analisar_oferta_com_ia(origem: str, destino: str, preco: float) -> str:
    """Gera uma análise completa: Dica + Hospedagem + Roteiro Curto"""
    if not client:
        return "Preço excelente! Garanta sua viagem."

    try:
        prompt = (
            f"Atue como um consultor de viagens expert. "
            f"Achei um voo de {origem} para {destino} por R$ {preco:.2f} (ida e volta no fim de semana). "
            f"1. Dê um motivo real por que esse preço está bom. "
            f"2. Sugira um bairro com bom custo-benefício para se hospedar em {destino}. "
            f"3. Sugira uma atividade gratuita ou barata para o sábado à tarde. "
            f"Seja direto, use no máximo 350 caracteres e alguns emojis."
        )
        
        # A nova forma de gerar conteúdo no pacote google-genai
        response = client.models.generate_content(
            model='gemini-1.5-flash',
            contents=prompt,
        )
        return response.text.strip()
    except Exception as e:
        return f"Oportunidade incrível para {destino}! O preço está abaixo da média de mercado. ✈️"

# Mantemos o nome da função que o seu monitor_passagens.py já conhece
gerar_dica_turismo = analisar_oferta_com_ia
