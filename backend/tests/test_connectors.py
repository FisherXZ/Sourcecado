from fastapi.testclient import TestClient

from coworker.connectors.google_oauth import (
    CALENDAR_SCOPE,
    COMPOSE_SCOPE,
    DRIVE_SCOPE,
    READ_SCOPE,
)
from coworker.server import TOKEN_HEADER, create_app

TOKEN = "test-token-connectors"


def test_connectors_never_include_secrets(tmp_path):
    app = create_app(token=TOKEN, state=tmp_path, apollo_key="sk-secret")
    app.state.secrets.put(
        "google",
        {
            "refresh_token": "rt-secret",
            "email": "fisher@example.com",
            "scopes": [
                "https://www.googleapis.com/auth/gmail.compose",
                "https://www.googleapis.com/auth/gmail.readonly",
                "https://www.googleapis.com/auth/userinfo.email",
            ],
        },
    )
    body = TestClient(app).get("/v1/connectors", headers={TOKEN_HEADER: TOKEN}).json()
    blob = str(body)
    assert "rt-secret" not in blob
    assert "sk-secret" not in blob
    by_id = {c["id"]: c for c in body["connectors"]}
    assert by_id["gmail"]["status"] == "connected"
    assert by_id["gmail"]["email"] == "fisher@example.com"
    assert by_id["drive"]["status"] == "missing_scopes"
    assert by_id["calendar"]["status"] == "missing_scopes"
    assert by_id["apollo"]["status"] == "connected"
    assert by_id["granola"]["status"] == "available"


def test_connectors_normalize_safe_health_scope_and_recovery_metadata(tmp_path):
    app = create_app(token=TOKEN, state=tmp_path, apollo_key="sk-secret")
    app.state.secrets.put(
        "google",
        {
            "refresh_token": "rt-secret",
            "access_token": "at-secret",
            "client_secret": "client-secret",
            "email": "fisher@example.com",
            "scopes": [COMPOSE_SCOPE, DRIVE_SCOPE],
        },
    )
    app.state.secrets.put(
        "mcp-oauth:granola",
        {
            "access_token": "granola-at-secret",
            "refresh_token": "granola-rt-secret",
            "client_id": "granola-client-secret",
        },
    )

    body = TestClient(app).get("/v1/connectors", headers={TOKEN_HEADER: TOKEN}).json()
    by_id = {connector["id"]: connector for connector in body["connectors"]}

    safe_fields = {
        "id",
        "title",
        "description",
        "status",
        "catalog_group",
        "email",
        "required_scopes",
        "missing_scopes",
        "health",
        "recovery",
        "supported_actions",
        "available_actions",
        "repair_route",
        "authorization_group",
    }
    assert all(set(connector) == safe_fields for connector in body["connectors"])
    assert set(by_id) == {"gmail", "drive", "calendar", "apollo", "granola"}

    gmail = by_id["gmail"]
    assert gmail["status"] == "missing_scopes"
    assert gmail["catalog_group"] == "connected"
    assert gmail["missing_scopes"] == ["Read Gmail messages"]
    assert gmail["health"]["category"] == "attention"
    assert gmail["recovery"]["category"] == "grant_scopes"
    assert gmail["available_actions"] == ["reconnect", "disconnect"]
    assert gmail["authorization_group"] == "google"

    drive = by_id["drive"]
    assert drive["status"] == "connected"
    assert drive["missing_scopes"] == []
    assert drive["email"] == "fisher@example.com"
    assert drive["supported_actions"] == ["Search files", "List folders", "Read files"]

    calendar = by_id["calendar"]
    assert calendar["status"] == "missing_scopes"
    assert calendar["missing_scopes"] == ["View and update calendar events"]

    assert by_id["apollo"]["status"] == "connected"
    assert by_id["apollo"]["health"]["label"] == "Configured"
    assert by_id["apollo"]["available_actions"] == ["view_guidance"]
    assert by_id["granola"]["status"] == "connected"

    serialized = str(body).lower()
    for secret in (
        "rt-secret",
        "at-secret",
        "client-secret",
        "sk-secret",
        "granola-at-secret",
        "granola-rt-secret",
        "granola-client-secret",
    ):
        assert secret not in serialized
    for forbidden_key in ("token", "secret", "credential", "api_key", "client_id"):
        assert forbidden_key not in serialized


def test_google_disconnect_reports_and_removes_all_shared_authorization(tmp_path):
    app = create_app(token=TOKEN, state=tmp_path)
    app.state.secrets.put(
        "google",
        {
            "refresh_token": "rt-secret",
            "email": "fisher@example.com",
            "scopes": [COMPOSE_SCOPE, READ_SCOPE, DRIVE_SCOPE, CALENDAR_SCOPE],
        },
    )
    client = TestClient(app)

    response = client.post("/v1/gmail/disconnect", headers={TOKEN_HEADER: TOKEN})

    assert response.json() == {
        "connected": False,
        "email": None,
        "disconnected": ["gmail", "drive", "calendar"],
    }
    catalog = client.get("/v1/connectors", headers={TOKEN_HEADER: TOKEN}).json()
    by_id = {connector["id"]: connector for connector in catalog["connectors"]}
    assert [by_id[name]["status"] for name in ("gmail", "drive", "calendar")] == [
        "available",
        "available",
        "available",
    ]


def test_granola_disconnect_is_scoped_to_granola(tmp_path):
    app = create_app(token=TOKEN, state=tmp_path)
    app.state.secrets.put(
        "google",
        {
            "refresh_token": "rt-secret",
            "email": "fisher@example.com",
            "scopes": [COMPOSE_SCOPE, READ_SCOPE],
        },
    )
    app.state.secrets.put("mcp-oauth:granola", {"access_token": "granola-secret"})
    client = TestClient(app)

    response = client.post(
        "/v1/connectors/granola/disconnect", headers={TOKEN_HEADER: TOKEN}
    )

    assert response.json() == {"connected": False, "disconnected": ["granola"]}
    catalog = client.get("/v1/connectors", headers={TOKEN_HEADER: TOKEN}).json()
    by_id = {connector["id"]: connector for connector in catalog["connectors"]}
    assert by_id["gmail"]["status"] == "connected"
    assert by_id["granola"]["status"] == "available"


def test_available_google_connectors_mark_every_required_scope_missing(tmp_path):
    app = create_app(token=TOKEN, state=tmp_path)

    body = TestClient(app).get("/v1/connectors", headers={TOKEN_HEADER: TOKEN}).json()
    by_id = {connector["id"]: connector for connector in body["connectors"]}

    for connector_id in ("gmail", "drive", "calendar"):
        connector = by_id[connector_id]
        assert connector["status"] == "available"
        assert connector["required_scopes"]
        assert connector["missing_scopes"] == connector["required_scopes"]
