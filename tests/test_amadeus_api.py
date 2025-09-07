import types
import monitor_passagens as m

class DummyResp:
    def __init__(self, status, body):
        self.status_code = status
        self._body = body
    def raise_for_status(self):
        if not (200 <= self.status_code < 300):
            raise Exception("http error")
    def json(self):
        return self._body

def test_get_token_ok(monkeypatch):
    m.CLIENT_ID = "test_id"
    m.CLIENT_SECRET = "test_secret"

    def fake_post(url, data=None, timeout=30, **kw):
        assert "oauth2/token" in url
        return DummyResp(200, {"access_token": "abc123"})

    fake_requests = types.SimpleNamespace(
        post=fake_post,
        RequestException=Exception
    )
    monkeypatch.setattr(m, "requests", fake_requests)

    token = m.get_token()
    assert token == "abc123"
    