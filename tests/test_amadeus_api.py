import types
import monitor_passagens as m

class DummyResp:
    def __init__(self, status_code=200, json_data=None, text="OK"):
        self.status_code = status_code
        self._json = json_data or {}
        self.text = text
    def json(self):
        return self._json
    def raise_for_status(self):
        if self.status_code >= 400:
            raise Exception(f"HTTP {self.status_code}")

def test_get_token_ok(monkeypatch):
    def fake_req(method, url, timeout=30, **kw):
        assert method == "POST"
        return DummyResp(200, {"access_token":"abc123"})
    monkeypatch.setattr(m, "requests", types.SimpleNamespace(request=fake_req))
    token = m.get_token()
    assert token == "abc123"

def test_buscar_passagens_ok(monkeypatch):
    def fake_req(method, url, timeout=60, **kw):
        assert method == "GET"
        return DummyResp(200, {"data":[{"price":{"total":"10.00","currency":"BRL"}, "itineraries":[{"segments":[{"departure":{"at":"2025-12-01T06:00:00"}}]}]}]})
    monkeypatch.setattr(m, "requests", types.SimpleNamespace(request=fake_req))
    out = m.buscar_passagens("tok", "GYN", "SSA", "2025-12-01")
    assert "data" in out and len(out["data"]) == 1
