import sqlite3
import logging
import os
from pathlib import Path

# ==========================================================
# CONFIGURAÇÃO CENTRAL DE LOGS
# ==========================================================
# Garantir que o caminho absoluto seja considerado para evitar erros no GitHub
BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        # Adicionado delay=True para evitar erros de abertura de arquivo em ambientes restritos
        logging.FileHandler(DATA_DIR / "app.log", encoding="utf-8", delay=True),
        logging.StreamHandler() 
    ]
)
logger = logging.getLogger("FlightMonitor")

# ==========================================================
# BASE DE DADOS SQLITE
# ==========================================================
DB_PATH = DATA_DIR / "passagens.db"

def init_db():
    """Inicializa a base de dados e cria a tabela de histórico se não existir."""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS historico (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT,
                origem TEXT,
                destino TEXT,
                data TEXT,
                preco REAL
            )
        """)
        conn.commit()
        conn.close()
        logger.info(f"Base de dados SQLite inicializada em: {DB_PATH}")
    except Exception as e:
        logger.error(f"Erro ao inicializar banco de dados: {e}")

def salvar_historico_db(row: dict):
    """Guarda um novo registo de preço na base de dados."""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        cursor.execute("""
            INSERT INTO historico (ts, origem, destino, data, preco)
            VALUES (:ts, :origem, :destino, :data, :preco)
        """, row)
        
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"Erro ao salvar no banco de dados: {e}")