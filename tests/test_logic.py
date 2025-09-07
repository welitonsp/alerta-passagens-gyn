import csv
from pathlib import Path
import monitor_passagens as m

def test_deve_alertar_por_teto():
    ok, motivo = m.deve_alertar(999.99, 2000.0)
    assert ok and "≤ teto" in motivo

def test_deve_alertar_por_queda():
    ok, motivo = m.deve_alertar(70.0, 100.0)
    assert ok and "queda" in motivo

def test_sem_alerta():
    ok, motivo = m.deve_alertar(1500.0, 1400.0)
    assert not ok and "sem queda" in motivo

def test_find_cheapest_offer_basico():
    offers = {
        "data": [
            {"price": {"total": "300.00", "currency": "BRL"}, "itineraries":[{"segments":[{"departure":{"at":"2025-10-10T10:00:00"}}]}]},
            {"price": {"total": "250.00", "currency": "BRL"}, "itineraries":[{"segments":[{"departure":{"at":"2025-10-11T08:00:00"}}]}]},
        ]
    }
    cheapest = m.find_cheapest_offer(offers)
    assert cheapest is not None
    assert float(cheapest["price"]["total"]) == 250.0
    assert "airline" in cheapest
