# telegram_bot.py
import os
import sqlite3
import requests
from database import DB_PATH, logger

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
BASE = f"https://api.telegram.org/bot{BOT_TOKEN}"

def send(msg: str):
    if not BOT_TOKEN or not CHAT_ID:
        return
    try:
        requests.post(
            f"{BASE}/sendMessage",
            json={
                "chat_id": CHAT_ID,
                "text": msg,
                "parse_mode": "HTML",
                "disable_web_page_preview": True
            },
            timeout=20
        )
    except Exception as e:
        logger.error(f"Erro ao enviar mensagem no Telegram: {e}")

def get_updates(offset=None):
    params = {"timeout": 10}
    if offset:
        params["offset"] = offset
    try:
        r = requests.get(f"{BASE}/getUpdates", params=params, timeout=20)
        if r.status_code == 200:
            return r.json().get("result", [])
    except Exception as e:
        logger.error(f"Erro ao buscar atualizações do Telegram: {e}")
    return []

def buscar_ultimos_registos():
    """Busca os últimos 5 registos na base de dados SQLite."""
    if not os.path.exists(DB_PATH):
        return "⚠️ Base de dados ainda não foi criada ou está vazia."
    
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT origem, destino, data, preco 
            FROM historico 
            ORDER BY id DESC LIMIT 5
        """)
        rows = cursor.fetchall()
        conn.close()
        
        if not rows:
            return "📭 Nenhum voo registado ainda."
            
        linhas = ["📊 <b>Últimos 5 voos monitorizados:</b>\n"]
        for r in rows:
            linhas.append(f"✈️ {r['origem']} → {r['destino']} ({r['data']}): <b>R$ {r['preco']:.2f}</b>")
            
        return "\n".join(linhas)
    except Exception as e:
        logger.error(f"Erro ao ler BD para o Telegram: {e}")
        return "❌ Erro ao ler a base de dados."

def handle_commands():
    updates = get_updates()
    last_id = None
    for u in updates:
        last_id = u["update_id"]
        msg = u.get("message", {})
        text = (msg.get("text") or "").strip().lower()
        
        if text == "/status":
            send("🟢 FlightHunter ativo e a utilizar base de dados SQLite.")
        elif text == "/ultimos":
            resposta = buscar_ultimos_registos()
            send(resposta)
            
    return last_id

if __name__ == "__main__":
    # Apenas para teste local
    handle_commands()
