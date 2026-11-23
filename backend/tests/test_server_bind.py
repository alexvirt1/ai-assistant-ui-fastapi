"""Where the backend listens."""


def test_backend_binds_loopback_by_default(monkeypatch):
    from app.server import bind_host

    monkeypatch.delenv("BACKEND_HOST", raising=False)
    assert bind_host() == "127.0.0.1"
    monkeypatch.setenv("BACKEND_HOST", "0.0.0.0")
    assert bind_host() == "0.0.0.0"
