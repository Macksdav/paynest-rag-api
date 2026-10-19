"""
API tests. They run in LLM_MODE=offline, so CI needs no GPU or model download.
Run:  pytest -v
"""

import os

os.environ["LLM_MODE"] = "offline"
os.environ.pop("API_KEY", None)

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture()
def client():
    # The "with" block runs the lifespan, which builds the RAG service.
    with TestClient(app) as c:
        yield c


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["mode"] == "offline"
    assert body["chunks"] > 0


def test_ask_returns_answer_and_sources(client):
    r = client.post("/ask", json={"question": "How long do instant transfers take?"})
    assert r.status_code == 200
    body = r.json()
    assert "30 seconds" in body["answer"]
    assert any("Instant transfers" in s for s in body["sources"])
    assert body["latency_ms"] >= 0


def test_unknown_question_gets_fallback(client):
    r = client.post("/ask", json={"question": "What is the card replacement fee?"})
    assert r.status_code == 200
    assert r.json()["answer"] == "I can't find that in our help centre."


def test_validation_rejects_short_question(client):
    r = client.post("/ask", json={"question": "hi"})
    assert r.status_code == 422


def test_validation_rejects_missing_field(client):
    r = client.post("/ask", json={})
    assert r.status_code == 422


def test_api_key_enforced_when_configured(client, monkeypatch):
    monkeypatch.setenv("API_KEY", "secret-123")
    body = {"question": "How long do instant transfers take?"}
    assert client.post("/ask", json=body).status_code == 401
    assert client.post("/ask", json=body, headers={"X-API-Key": "wrong"}).status_code == 401
    assert client.post("/ask", json=body, headers={"X-API-Key": "secret-123"}).status_code == 200
    # Health stays open so load balancers can probe it.
    assert client.get("/health").status_code == 200


def test_llm_failure_returns_503(client, monkeypatch):
    async def boom(question):
        raise RuntimeError("Ollama is down")

    monkeypatch.setattr(client.app.state.rag, "ask", boom)
    r = client.post("/ask", json={"question": "How long do instant transfers take?"})
    assert r.status_code == 503
    assert "temporarily unavailable" in r.json()["detail"]


def test_root_serves_chat_ui(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "PayNest" in r.text


def test_static_assets_are_served(client):
    assert client.get("/static/index.html").status_code == 200
