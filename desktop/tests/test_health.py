from fastapi.testclient import TestClient

from coworker.server import TOKEN_HEADER, create_app

TOKEN = "test-token-slice-1"


def client(tmp_path) -> TestClient:
    return TestClient(create_app(token=TOKEN, state=tmp_path))


def test_health_ok_with_token(tmp_path):
    res = client(tmp_path).get("/v1/health", headers={TOKEN_HEADER: TOKEN})
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "ok"
    assert body["piece"] == "backend"
    assert body["slice"] == 29
    assert "model" in body


def test_hello_ok_with_token(tmp_path):
    res = client(tmp_path).get("/v1/hello", headers={TOKEN_HEADER: TOKEN})
    assert res.status_code == 200
    assert res.json()["piece"] == "brain"


def test_health_unauthorized_without_token(tmp_path):
    res = client(tmp_path).get("/v1/health")
    assert res.status_code == 401
    assert res.json()["error"] == "unauthorized"


def test_health_unauthorized_wrong_token(tmp_path):
    res = client(tmp_path).get("/v1/health", headers={TOKEN_HEADER: "nope"})
    assert res.status_code == 401


def test_create_app_rejects_empty_token():
    try:
        create_app(token="")
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_options_preflight_does_not_need_token(tmp_path):
    res = client(tmp_path).options(
        "/v1/health",
        headers={
            "Origin": "http://localhost:5180",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "x-club-token",
        },
    )
    assert res.status_code in (200, 204)
    assert res.headers.get("access-control-allow-origin") == "http://localhost:5180"
