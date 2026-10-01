from fastapi.testclient import TestClient

from apps.api.main import create_app


def test_missing_api_key_is_rejected(monkeypatch):
    monkeypatch.setenv("API_KEY", "secret-key")
    with TestClient(create_app()) as client:
        denied = client.get("/api/v1/does-not-exist")
        assert denied.status_code == 401
        allowed = client.get("/api/v1/does-not-exist", headers={"X-API-Key": "secret-key"})
        assert allowed.status_code == 404
        health = client.get("/health")
        assert health.status_code == 200


def test_empty_api_key_leaves_api_open(monkeypatch):
    monkeypatch.setenv("API_KEY", "")
    with TestClient(create_app()) as client:
        assert client.get("/api/v1/does-not-exist").status_code == 404
