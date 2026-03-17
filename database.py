import sqlite3
import logging
from pathlib import Path

# ==========================================================
# CONFIGURAÇÃO CENTRAL DE LOGS
# ==========================================================
Path("data").mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("data/app.log", encoding="utf-8"),
        logging.StreamHandler() # Mostra no terminal
    ]
)
logger = logging.getLogger("FlightMonitor")

# ==========================================================
# BASE DE DADOS SQLITE
# ==========================================================
DB_PATH = "data/passagens.db"

def init_db():
    """Inicializa a base de dados e cria a tabela de histórico se não existir."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Adaptado para suportar as colunas já usadas nos seus scripts de relatório
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
    logger.info("Base de dados SQLite inicializada.")

def salvar_historico_db(row: dict):
    """Guarda um novo registo de preço na base de dados."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute("""
        INSERT INTO historico (ts, origem, destino, data, preco)
        VALUES (:ts, :origem, :destino, :data, :preco)
    """, row)
    
    conn.commit()
    conn.close()
