import json
import time

from fastapi import FastAPI, Request, WebSocket
from fastapi.testclient import TestClient

from app.security.local_auth import (
    LocalRequestSigner,
    SignedLocalAuthMiddleware,
    generate_session_secret,
)


def make_client(security_mode="production"):
    secret = generate_session_secret()
    app = FastAPI()
    app.add_middleware(
        SignedLocalAuthMiddleware,
        security_mode=security_mode,
        session_secret=secret,
        max_skew_seconds=30,
    )

    @app.post("/echo")
    async def echo(request: Request):
        return {"body": (await request.body()).decode()}

    @app.get("/query")
    async def query(value: str):
        return {"value": value}

    @app.websocket("/ws")
    async def ws(socket: WebSocket):
        await socket.accept()
        await socket.send_text("ok")
        await socket.close()

    return TestClient(app), LocalRequestSigner(secret)


def test_http_body_and_query_are_covered_by_signature():
    client, signer = make_client()
    body = json.dumps({"value": "signed"}, separators=(",", ":")).encode()
    response = client.post(
        "/echo",
        content=body,
        headers=signer.headers(method="POST", path="/echo", body=body),
    )
    assert response.status_code == 200
    assert response.json()["body"] == body.decode()

    headers = signer.headers(method="GET", path="/query", query="value=one")
    assert client.get("/query?value=one", headers=headers).status_code == 200
    assert (
        client.get(
            "/query?value=two",
            headers=signer.headers(method="GET", path="/query", query="value=one"),
        ).status_code
        == 401
    )


def test_missing_tampered_expired_and_replayed_requests_fail_closed():
    client, signer = make_client()
    assert client.post("/echo", content=b"hello").status_code == 401

    headers = signer.headers(method="POST", path="/echo", body=b"hello")
    assert client.post("/echo", content=b"changed", headers=headers).status_code == 401

    replay_headers = signer.headers(method="POST", path="/echo", body=b"hello")
    assert (
        client.post("/echo", content=b"hello", headers=replay_headers).status_code
        == 200
    )
    assert (
        client.post("/echo", content=b"hello", headers=replay_headers).status_code
        == 401
    )

    stale = signer.headers(
        method="POST", path="/echo", body=b"hello", timestamp=int(time.time()) - 120
    )
    assert client.post("/echo", content=b"hello", headers=stale).status_code == 401


def test_websocket_upgrade_requires_signed_session_headers():
    client, signer = make_client()
    headers = signer.headers(method="WS", path="/ws")
    with client.websocket_connect("/ws", headers=headers) as socket:
        assert socket.receive_text() == "ok"


def test_development_mode_is_explicitly_unlocked():
    app = FastAPI()
    app.add_middleware(
        SignedLocalAuthMiddleware, security_mode="development", session_secret=""
    )

    @app.get("/")
    def root():
        return {"ok": True}

    assert TestClient(app).get("/").status_code == 200


def test_friends_distribution_keeps_signed_local_authentication():
    client, signer = make_client("friends")
    assert client.get("/query?value=one").status_code == 401
    headers = signer.headers(method="GET", path="/query", query="value=one")
    assert client.get("/query?value=one", headers=headers).status_code == 200
