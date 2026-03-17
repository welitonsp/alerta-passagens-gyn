import os
import google.generativeai as genai
from dotenv import load_dotenv

load_dotenv()

# Configura a chave da API
CHAVE_API = os.getenv("GEMINI_API_KEY")
if CHAVE_API:
    genai.configure(api_key=CHAVE_API)

def gerar_dica_turismo(origem: str, destino: str, preco: float) -> str:
    """Gera uma dica persuasiva usando o modelo Gemini 1.5 Flash"""
    if not CHAVE_API:
        return "Prepare as malas! Uma viagem incrível te espera."

    try:
        model = genai.GenerativeModel('gemini-1.5-flash')
        prompt = (
            f"Aja como um agente de viagens animado. "
            f"Crie uma dica curta (máximo 2 frases) para alguém que acabou de achar "
            f"uma passagem barata de {origem} para {destino} por apenas R$ {preco:.2f}. "
            f"Use 1 ou 2 emojis."
        )
        response = model.generate_content(prompt)
        return response.text.strip()
    except Exception as e:
        print(f"Erro na IA: {e}")
        return f"Voo barato encontrado para {destino}! Garanta antes que acabe."