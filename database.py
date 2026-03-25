import os
import psycopg2 # Biblioteca nova para conectar no PostgreSQL
import logging
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# ==========================================================
# CONFIGURAÇÃO DE LOGS (Mantivemos igual ao seu)
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
# Aqui ele vai puxar aquela URL que salvamos no GitHub Secrets
DATABASE_URL = os.getenv("DATABASE_URL")

def get_connection():
    """Cria a conexão com o banco de dados Supabase."""
    if not DATABASE_URL:
        logger.error("DATABASE_URL não configurada nas variáveis de ambiente.")
        return None
    return psycopg2.connect(DATABASE_URL)

def init_db():
    """Cria a tabela no PostgreSQL na nuvem se ela não existir."""
    conn = get_connection()
    if not conn:
        return
        
    try:
        cursor = conn.cursor()
        # O SERIAL no Postgres faz a mesma coisa que o AUTOINCREMENT no SQLite
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
        conn.commit()
        logger.info("Tabela 'historico' verificada/criada com sucesso no Supabase.")
    except Exception as e:
        logger.error(f"Erro ao inicializar banco de dados: {e}")
    finally:
        cursor.close()
        conn.close()

def salvar_historico_db(row: dict):
    """Guarda um novo registro de preço na nuvem."""
    conn = get_connection()
    if not conn:
        return

    try:
        cursor = conn.cursor()
        # No Postgres, usamos %s no lugar de :nome para passar variáveis
        cursor.execute("""
            INSERT INTO historico (ts, origem, destino, data, preco)
            VALUES (%s, %s, %s, %s, %s)
        """, (row['ts'], row['origem'], row['destino'], row['data'], row['preco']))
        
        conn.commit()
        logger.info(f"💾 Salvo no banco: {row['origem']}->{row['destino']} por R${row['preco']}")
    except Exception as e:
        logger.error(f"Erro ao salvar no banco de dados: {e}")
    finally:
        cursor.close()
        conn.close()
