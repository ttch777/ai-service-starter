"""端到端契约测试：不依赖真实 API Key，因此 CI 里也能跑。"""

import pytest
from fastapi.testclient import TestClient

from app.api.routes import get_registry
from app.main import app

CREDENTIAL_ENVS = ("DEEPSEEK_API_KEY", "DASHSCOPE_API_KEY", "ZHIPU_API_KEY")


@pytest.fixture
def client(monkeypatch):
    for name in CREDENTIAL_ENVS:
        monkeypatch.delenv(name, raising=False)
    get_registry.cache_clear()
    with TestClient(app) as test_client:
        yield test_client
    get_registry.cache_clear()


def test_health_returns_ok_and_trace_id(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] in {"ok", "degraded"}
    assert body["default_model"] == "deepseek-chat"
    assert resp.headers["x-trace-id"]


def test_models_does_not_leak_credentials(client):
    resp = client.get("/models")
    assert resp.status_code == 200
    text = resp.text.lower()
    for leak in ("api_key\"", "sk-", "secret"):
        assert leak not in text
    names = [m["name"] for m in resp.json()["models"]]
    assert {"deepseek-chat", "qwen-plus", "glm-4-flash"} <= set(names)


def test_chat_rejects_empty_messages(client):
    resp = client.post("/chat", json={"messages": []})
    assert resp.status_code == 422


def test_chat_rejects_invalid_role(client):
    resp = client.post("/chat", json={"messages": [{"role": "root", "content": "hi"}]})
    assert resp.status_code == 422


def test_unknown_model_returns_400(client):
    resp = client.post(
        "/chat", json={"messages": [{"role": "user", "content": "hi"}], "model": "gpt-9"}
    )
    assert resp.status_code == 400


def test_chat_without_credentials_returns_503(client):
    resp = client.post("/chat", json={"messages": [{"role": "user", "content": "hi"}]})
    assert resp.status_code == 503


def test_stream_still_closes_cleanly_without_credentials(client):
    with client.stream(
        "POST", "/chat/stream", json={"messages": [{"role": "user", "content": "hi"}]}
    ) as resp:
        body = "".join(resp.iter_text())
    assert '"type": "error"' in body
    assert body.rstrip().endswith("[DONE]")
