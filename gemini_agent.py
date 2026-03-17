import os
import google.generativeai as genai
from dotenv import load_dotenv

load_dotenv()

# Configura a chave da API
CHAVE_API = os.getenv("GEMINI_API_KEY")
if CHAVE_API:
    genai.configure(api_key=CHAVE_API)

def obter_modelo_disponivel():
    """Busca dinamicamente o modelo correto que a sua chave tem acesso."""
    try:
        # Pede ao Google a lista de modelos disponíveis para a sua chave
        for m in genai.list_models():
            if 'generateContent' in m.supported_generation_methods:
                return m.name # Retorna o nome exato (ex: 'models/gemini-1.0-pro')
    except Exception as e:
        print(f"Erro ao listar modelos: {e}")
    return None

def gerar_dica_turismo(origem: str, destino: str, preco: float) -> str:
    """Gera uma dica persuasiva usando o modelo dinâmico"""
    if not CHAVE_API:
        return "Prepare as malas! Uma viagem incrível te espera."

    nome_modelo = obter_modelo_disponivel()
    
    if not nome_modelo:
        return f"Voo barato encontrado para {destino}! Garanta antes que acabe."

    try:
        # Usa o modelo exato que o Google autorizou
        model = genai.GenerativeModel(nome_modelo)
        prompt = (
            f"Aja como um agente de viagens animado. "
            f"Crie uma dica curta (máximo 2 frases) para alguém que acabou de achar "
            f"uma passagem barata de {origem} para {destino} por apenas R$ {preco:.2f}. "
            f"Use 1 ou 2 emojis."
        )
        response = model.generate_content(prompt)
        return response.text.strip()
    except Exception as e:
        print(f"Erro na IA com o modelo {nome_modelo}: {e}")
        return f"Voo barato encontrado para {destino}! Garanta antes que acabe."