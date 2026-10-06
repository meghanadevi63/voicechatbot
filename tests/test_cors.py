from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.main import add_cors, parse_origins

ALLOWED = "http://localhost:5173"


def make_app(origins: list[str]) -> TestClient:
    app = FastAPI()

    @app.post("/api/tts")
    def tts():
        return {"ok": True}

    add_cors(app, origins)
    return TestClient(app)


def preflight(client: TestClient, origin: str):
    return client.options(
        "/api/tts",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )


def test_parse_origins():
    assert parse_origins("") == []
    assert parse_origins(" https://a.com/ , http://b.com:5173 ,") == ["https://a.com", "http://b.com:5173"]


def test_no_origins_means_no_cors_headers():
    client = make_app([])
    res = client.post("/api/tts", headers={"Origin": ALLOWED})
    assert res.status_code == 200
    assert "access-control-allow-origin" not in res.headers


def test_allowed_origin_gets_preflight_and_response_headers():
    client = make_app([ALLOWED])
    pre = preflight(client, ALLOWED)
    assert pre.status_code == 200
    assert pre.headers["access-control-allow-origin"] == ALLOWED
    assert "POST" in pre.headers["access-control-allow-methods"]
    assert pre.headers["access-control-max-age"] == "600"

    res = client.post("/api/tts", headers={"Origin": ALLOWED})
    assert res.headers["access-control-allow-origin"] == ALLOWED


def test_other_origin_is_rejected():
    client = make_app([ALLOWED])
    assert preflight(client, "https://evil.example").status_code == 400
    res = client.post("/api/tts", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in res.headers
