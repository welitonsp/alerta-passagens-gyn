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

def analisar_oferta_com_ia(origem: str, destino: str, preco: float, teto_alerta: float = 0.0, status_promo: str = "") -> str:
    """Gera análise de oferta baseada em dados estatísticos reais do baseline."""
    if not client:
        return "Preço excelente! Garanta sua viagem."

    economia = teto_alerta - preco
    percentual_economia = (economia / teto_alerta * 100) if teto_alerta > 0 else 0

    if percentual_economia >= 30:
        urgencia = "🚨 ALERTA DE PROMOÇÃO REAL 🚨"
    elif percentual_economia >= 15:
        urgencia = "✅ Boa oportunidade detectada"
    elif percentual_economia >= 5:
        urgencia = "👀 Preço levemente abaixo do normal"
    else:
        urgencia = "ℹ️ Preço dentro do esperado"

    if teto_alerta > 0:
        contexto_stats = (
            f"Preço atual: R$ {preco:.2f}/pessoa | "
            f"Preço de referência: R$ {teto_alerta:.2f} | "
            f"Economia: R$ {economia:.2f} ({percentual_economia:.0f}%) | "
            f"Classificação: {status_promo}"
        )
        instrucao = (
            f"1. Se economia ≥ 15%: explica que é oportunidade real (cite os valores). "
            f"2. Se economia < 15%: informa que está dentro do normal. "
        )
    else:
        contexto_stats = f"Preço atual: R$ {preco:.2f}/pessoa"
        instrucao = "1. Dê um motivo real por que esse preço está bom. "

    prompt = (
        f"Atue como consultor de tarifas aéreas. "
        f"Rota: {origem} → {destino}. {contexto_stats}. "
        f"{instrucao}"
        f"3. Adiciona 1 dica turística gratuita ou barata em {destino} perfeita para um casal com duas crianças de 8 e 4 anos. "
        f"Máximo 300 caracteres. Use emojis. "
        f"Formato: [{urgencia}] / [Análise] / [Dica turística]"
    )

    try:
        response = client.models.generate_content(
            model='gemini-1.5-flash',
            contents=prompt,
        )
        return response.text.strip()
    except Exception as e:
        return f"{urgencia}\n{origem}→{destino}: R${preco:.2f} (Ref: R${teto_alerta:.2f})"

gerar_dica_turismo = analisar_oferta_com_ia
