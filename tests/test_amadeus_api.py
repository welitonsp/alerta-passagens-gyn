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
    # garante que o módulo não chame sys.exit por falta de credenciais
    m.CLIENT_ID = "test_id"
    m.CLIENT_SECRET = "test_secret"

    def fake_req(method, url, timeout=30, **kw):
        assert method == "POST"
        return DummyResp(200, {"access_token": "abc123"})

    # troca apenas o 'requests.request' usado internamente
    monkeypatch.setattr(m, "requests", types.SimpleNamespace(request=fake_req))

    token = m.get_token()
    assert token == "abc123"