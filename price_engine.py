# price_engine.py
import sqlite3
from statistics import mean
from database import DB_PATH

def load_prices(origem: str, destino: str):
    """Carrega todo o histórico de preços de uma rota específica a partir da BD."""
    prices = []
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        cursor.execute(
            "SELECT preco FROM historico WHERE origem = ? AND destino = ?", 
            (origem, destino)
        )
        
        rows = cursor.fetchall()
        prices = [float(r[0]) for r in rows if r[0] is not None]
        conn.close()
    except Exception:
        pass
    
    return prices

def media_historica(prices):
    return mean(prices) if prices else None

def prever_preco(prices):
    """Previsão simples: média das últimas 10 pesquisas (ou média geral se houver menos)."""
    if not prices:
        return None
    last = prices[-10:] if len(prices) >= 10 else prices
    return mean(last)

def score_promocao(preco_atual: float, preco_previsto: float | None):
    """Calcula a percentagem de quão abaixo do preço esperado está a passagem."""
    if not preco_previsto or preco_previsto <= 0:
        return 0.0
    diff = (preco_previsto - preco_atual) / preco_previsto
    return round(diff * 100, 1)  # % abaixo do esperado
