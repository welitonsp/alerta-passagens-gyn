import monitor_passagens as m

def test_deve_alertar_por_teto():
    # Teto alto deve gerar alerta por teto
    m.MAX_PRECO_PP = 1000.0
    ok, motivo = m.deve_alertar(preco_atual=900.0, melhor_anterior=2000.0)
    assert ok is True
    assert "teto" in motivo or "≤" in motivo

def test_deve_alertar_por_queda_percentual():
    # Para testar queda, forçamos teto BEM BAIXO pra não pegar a regra do teto
    m.MAX_PRECO_PP = 10.0
    ok, motivo = m.deve_alertar(preco_atual=70.0, melhor_anterior=100.0)
    assert ok is True
    assert "queda" in motivo or "%" in motivo

def test_sem_alerta_quando_mais_caro_e_sem_teto():
    # Sem teto e sem queda suficiente => não deve alertar
    m.MAX_PRECO_PP = 10.0
    ok, motivo = m.deve_alertar(preco_atual=1500.0, melhor_anterior=1400.0)
    assert ok is False
    assert "sem" in motivo  # "sem queda/teto" etc.

def test_find_cheapest_offer_basico():
    # Deve pegar a menor tarifa e não quebrar se faltar dados de companhia
    offers = {
        "data": [
            {
                "price": {"total": "300.00", "currency": "BRL"},
                "itineraries": [{"segments": [{"departure": {"at": "2025-10-10T10:00:00"}}]}],
            },
            {
                "price": {"total": "250.00", "currency": "BRL"},
                "itineraries": [{"segments": [{"departure": {"at": "2025-10-11T08:00:00"}}]}],
            },
        ]
    }
    cheapest = m.find_cheapest_offer(offers)
    assert cheapest is not None
    assert float(cheapest["price"]["total"]) == 250.0
    # Função adiciona 'airline' mesmo que não tenha vindo do JSON
    assert "airline" in cheapest