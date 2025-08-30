# No seu repositório local
cat > monitor_passagens.py << 'EOF'
import os
import requests
from datetime import datetime, timedelta
# Configurações
BOT_TOKEN = os.environ.get('BOT_TOKEN')
CHAT_ID = os.environ.get('CHAT_ID')
def enviar_telegram(mensagem):
    """Envia mensagem via Telegram"""
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": mensagem,
        "parse_mode": "HTML"
    }
    try:
        response = requests.post(url, json=payload, timeout=10)
        return response.status_code == 200
    except:
        return False
def simular_busca():
    """Simula busca de passagens"""
    destinos = ["FOR", "SSA", "REC", "NAT", "BSB", "CGH", "GIG"]
    ofertas = []
    
    for destino in destinos:
        # Simulação de preço
        preco_base = 800 if destino in ["FOR", "SSA", "REC"] else 1000
        preco = preco_base + (hash(f"{destino}{datetime.now().day}") % 200)
        
        if preco < 1000:
            ofertas.append({
                'destino': destino,
                'preco': preco,
                'datas': f"{(datetime.now() + timedelta(days=10)).strftime('%d/%m')} - {(datetime.now() + timedelta(days=17)).strftime('%d/%m')}"
            })
    
    return ofertas
def main():
    print(f"Iniciando monitoramento em {datetime.now()}")
    
    # Buscar passagens
    ofertas = simular_busca()
    
    # Enviar alertas
    for oferta in ofertas:
        mensagem = f"""
🚨 <b>PROMOÇÃO DETECTADA!</b>
📍 <b>GYN → {oferta['destino']}</b>
📅 <b>Datas:</b> {oferta['datas']}
💰 <b>Preço total (4 pessoas):</b> R$ {oferta['preco']},00
👥 <b>2 Adultos + 2 Crianças</b>
🔗 <b>Verifique no site da companhia</b>
<i>Monitorado via GitHub Actions</i>
"""
        if enviar_telegram(mensagem):
            print(f"Alerta enviado para {oferta['destino']}")
        else:
            print(f"Falha ao enviar alerta para {oferta['destino']}")
    
    if not ofertas:
        print("Nenhuma promoção encontrada")
if __name__ == "__main__":
    main()
EOF

# Fazer commit e push
git add monitor_passagens.py
git commit -m "Adicionar script de monitoramento de passagens"
git push origin main
