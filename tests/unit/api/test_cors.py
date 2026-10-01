"""Browser preflight requests must succeed only for configured origins."""

from fastapi.testclient import TestClient

from apps.api.main import create_app


def test_configured_frontend_can_send_json(monkeypatch):
    monkeypatch.setenv("CORS_ORIGINS", '["http://localhost:3000"]')
    with TestClient(create_app()) as client:
        response = client.options(
            "/api/v1/projects",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type",
            },
        )
        assert response.status_code == 200
        assert response.headers["access-control-allow-origin"] == "http://localhost:3000"
        response = client.options(
            "/api/v1/projects",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "DELETE",
                "Access-Control-Request-Headers": "content-type,x-api-key",
            },
        )
        assert response.status_code == 200
        assert "DELETE" in response.headers.get("access-control-allow-methods", "")


def test_unconfigured_origin_is_not_allowed(monkeypatch):
    monkeypatch.setenv("CORS_ORIGINS", '["http://localhost:3000"]')
    with TestClient(create_app()) as client:
        response = client.options(
            "/api/v1/projects",
            headers={"Origin": "https://other.example", "Access-Control-Request-Method": "POST"},
        )
        assert response.status_code == 400
        assert "access-control-allow-origin" not in response.headers
