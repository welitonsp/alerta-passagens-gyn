import os
import psycopg2 
import logging
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# ==========================================================
# CONFIGURAÇÃO DE LOGS
# ==========================================================
BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(DATA_DIR / "app.log", encoding="utf-8", delay=True),
        logging.StreamHandler() 
    ]
)
logger = logging.getLogger("FlightMonitor")

# ==========================================================
# CONEXÃO COM O BANCO EM NUVEM (POSTGRESQL)
# ==========================================================
DATABASE_URL = os.getenv("DATABASE_URL")

def get_connection():
    if not DATABASE_URL:
        logger.error("DATABASE_URL não configurada.")
        return None
    return psycopg2.connect(DATABASE_URL)

def init_db():
    conn = get_connection()
    if not conn: return
        
    try:
        cursor = conn.cursor()
        # Tabela 1: Histórico completo (Escavadeira)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS historico (
                id SERIAL PRIMARY KEY,
                ts TEXT,
                origem TEXT,
                destino TEXT,
                data TEXT,
                preco REAL
            )
        """)
        
        # Tabela 2: NOVO! Controle de Duplicidade (Filtro Anti-Spam)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS alertas_enviados (
                hash_id TEXT PRIMARY KEY,
                enviado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.commit()
    except Exception as e:
        logger.error(f"Erro ao inicializar banco de dados: {e}")
    finally:
        cursor.close()
        conn.close()

def salvar_historico_db(row: dict):
    conn = get_connection()
    if not conn: return

    try:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO historico (ts, origem, destino, data, preco)
            VALUES (%s, %s, %s, %s, %s)
        """, (row['ts'], row['origem'], row['destino'], row['data'], row['preco']))
        conn.commit()
    except Exception as e:
        logger.error(f"Erro ao salvar no histórico: {e}")
    finally:
        cursor.close()
        conn.close()

# ==========================================================
# NOVAS FUNÇÕES: CONTROLE DE DUPLICIDADE (HASH)
# ==========================================================
def verificar_alerta_duplicado(hash_id: str) -> bool:
    """Verifica se esse alerta exato já foi enviado nas últimas 24 horas."""
    conn = get_connection()
    if not conn: return False
    
    try:
        with conn.cursor() as cursor:
            # Procura o hash nas últimas 24h
            cursor.execute("""
                SELECT 1 FROM alertas_enviados 
                WHERE hash_id = %s 
                AND enviado_em > CURRENT_TIMESTAMP - INTERVAL '24 hours'
            """, (hash_id,))
            return cursor.fetchone() is not None
    except Exception as e:
        logger.error(f"Erro ao verificar duplicidade: {e}")
        return False
    finally:
        conn.close()

def registrar_alerta(hash_id: str):
    """Grava o envio do alerta. Se já existir, atualiza a data para agora (UPSERT)."""
    conn = get_connection()
    if not conn: return
    
    try:
        with conn.cursor() as cursor:
            cursor.execute("""
                INSERT INTO alertas_enviados (hash_id, enviado_em)
                VALUES (%s, CURRENT_TIMESTAMP)
                ON CONFLICT (hash_id) 
                DO UPDATE SET enviado_em = CURRENT_TIMESTAMP
            """, (hash_id,))
        conn.commit()
    except Exception as e:
        logger.error(f"Erro ao registrar alerta: {e}")
    finally:
        conn.close()
