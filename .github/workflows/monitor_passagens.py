#!/usr/bin/env python3
import os
import sys
import requests
import hashlib
from datetime import datetime, timedelta, timezone

# ===== Config (via variáveis de ambiente do GitHub Actions) =====
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID   = os.getenv("CHAT_ID")             # pode ser string
ORIGEM    = os.getenv("ORIGEM", "GYN").upper()
DESTINOS  = [d.strip().upper() for d in os.getenv(
    "DESTINOS", "FOR,SSA,REC,NAT,BSB,CGH,GIG"
).split(",") if d.strip()]

# ===== Utilidades =====
def brl(n: float) -> str:
    """Formata número em R$ estilo pt-BR (sem locale)."""
    s = f"{n:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"R$ {s}"

def log(msg: str):
    print(f"[{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}] {msg}")

def enviar_telegram(texto: str) -> bool:
    if not BOT_TOKEN or not CHAT_ID:
        log("ERRO: Defina BOT_TOKEN e CHAT_ID nas variáveis de ambiente.")
        return False
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": texto, "parse_mode": "HTML", "disable_web_page_preview": True}
    try:
        r = requests.post(url, json=payload, timeout=20)
        if r.status_code != 200:
            log(f"Falha Telegram HTTP {r.status_code}: {r.text[:300]}")
            return False
        data = r.json()
        if not data.get("ok", False):
            log(f"Falha Telegram payload: {data}")
            return False
        return True
    except Exception as e:
        log(f"Exceção ao enviar Telegram: {e}")
        return False

def preco_simulado(base: int, chave: str) -> int:
    """Preço pseudoestável por dia/destino usando SHA256."""
    h = int(hashlib.sha256(chave.encode()).hexdigest(), 16)
    return base + (h % 200)  # flutuação 0..199

def simular_busca():
    """Simula busca de passagens saindo de ORIGEM para DESTINOS."""
    hoje = datetime.now(timezone.utc).date()
    ofertas = []
    for destino in DESTINOS:
        base = 800 if destino in {"FOR", "SSA", "REC"} else 1000
        preco_ida_volta_por_pessoa = preco_simulado(base, f"{destino}-{hoje.isoformat()}")
        if preco_ida_volta_por_pessoa < 1000:
            ida   = (hoje + timedelta(days=10)).strftime("%d/%m")
            volta = (hoje + timedelta(days=17)).strftime("%d/%m")
            ofertas.append({
                "origem": ORIGEM,
                "destino": destino,
                "preco_pp": float(preco_ida_volta_por_pessoa),
                "preco_total_4": float(preco_ida_volta_por_pessoa * 4),
                "datas": f"{ida} – {volta}",
            })
    return ofertas

def main():
    if not BOT_TOKEN or not CHAT_ID:
        print("ERRO: defina BOT_TOKEN e CHAT_ID (segredos do Actions).", file=sys.stderr)
        sys.exit(1)

    log(f"Iniciando monitoramento | origem={ORIGEM} destinos={','.join(DESTINOS)}")
    ofertas = simular_busca()

    if not ofertas:
        log("Nenhuma promoção encontrada.")
        return

    for o in ofertas:
        mensagem = (
            "🚨 <b>PROMOÇÃO DETECTADA!</b>\n"
            f"📍 <b>{o['origem']} → {o['destino']}</b>\n"
            f"📅 <b>Datas:</b> {o['datas']}\n"
            f"👥 <b>2 Adultos + 2 Crianças</b>\n"
            f"💰 <b>Preço por pessoa:</b> {brl(o['preco_pp'])}\n"
            f"💰 <b>Total (4 pessoas):</b> {brl(o['preco_total_4'])}\n"
            "🔗 <i>Verifique no site/APP da companhia</i>\n"
            "<i>Monitorado via GitHub Actions</i>"
        )
        ok = enviar_telegram(mensagem)
        log(f"Envio para {o['destino']}: {'OK' if ok else 'FALHOU'}")

if __name__ == "__main__":
    main()
