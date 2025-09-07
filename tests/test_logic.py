import monitor_passagens as m

def test_deve_alertar_por_queda_percentual():
    # força teto baixo para garantir priorização de 'queda'
    m.MAX_PRECO_PP = 10.0
    m.MIN_DISCOUNT_PCT = 0.20  # 20%
    ok, motivo = m.deve_alertar(preco_atual=70.0, melhor_anterior=100.0)
    assert ok is True
    assert "queda" in motivo or "%" in motivo

def test_deve_alertar_por_teto():
    m.MAX_PRECO_PP = 1200.0
    ok, motivo = m.deve_alertar(preco_atual=500.0, melhor_anterior=None)
    assert ok is True
    assert "teto" in motivo

def test_sem_alerta():
    m.MAX_PRECO_PP = 800.0
    m.MIN_DISCOUNT_PCT = 0.20
    ok, motivo = m.deve_alertar(preco_atual=1500.0, melhor_anterior=1400.0)
    assert ok is False
    assert "sem queda" in motivo or "acima do teto" in motivo