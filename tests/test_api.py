import pytest
from fastapi.testclient import TestClient

from mktg_copilot.api.app import create_app


@pytest.fixture(scope="module")
def client(db_path):
    return TestClient(create_app(db_path))


def test_health(client):
    j = client.get("/api/health").json()
    assert j["status"] == "ok" and j["as_of"] == "2026-09-27" and j["llm_planner"] is False


def test_catalog_lists_metrics_and_sample_questions(client):
    j = client.get("/api/catalog").json()
    keys = {m["key"] for m in j["metrics"]}
    assert {"roas", "cac", "revenue", "cvr"} <= keys and len(j["questions"]) >= 6
    assert any(m["formula"] == "SUM(revenue) / SUM(spend)" for m in j["metrics"])


def test_ask_returns_the_full_contract(client):
    r = client.post("/api/ask", json={"question": "Why did display CAC go up in August?"})
    assert r.status_code == 200
    j = r.json()
    for k in ("status", "headline", "narrative", "plan", "tables", "charts", "sql", "assumptions", "grounding", "followups", "timings_ms"):
        assert k in j
    assert j["status"] == "ok" and j["grounding"]["passed"] and "facts" not in j


def test_input_validation(client):
    assert client.post("/api/ask", json={"question": ""}).status_code == 422
    assert client.post("/api/ask", json={"question": "x" * 501}).status_code == 422
    assert client.post("/api/ask", json={"question": "revenue", "planner": "gpt"}).status_code == 422
    assert client.post("/api/ask", json={}).status_code == 422


def test_refusals_are_200_with_a_status(client):
    j = client.post("/api/ask", json={"question": "what was our profit last month"}).json()
    assert j["status"] == "refused" and j["suggestions"]


def test_overview_and_anomaly_feed(client):
    assert len(client.get("/api/overview").json()["cards"]) == 6
    assert len(client.get("/api/anomalies").json()["incidents"]) >= 5


def test_write_methods_are_not_exposed(client):
    for path in ("/api/ask", "/api/health", "/api/catalog"):
        assert client.delete(path).status_code in (404, 405)
    assert client.put("/api/ask", json={}).status_code == 405
