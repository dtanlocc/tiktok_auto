from __future__ import annotations

import shutil
import time
from pathlib import Path

from fastapi.testclient import TestClient

from invisible_browser_studio.infrastructure.config import Settings
from invisible_browser_studio.main import create_app


def wait_until_running(client: TestClient, session_id: str) -> dict[str, object]:
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        response = client.get(f"/api/v1/sessions/{session_id}")
        response.raise_for_status()
        body = response.json()
        if body["status"] == "running":
            return body
        time.sleep(0.01)
    raise AssertionError("session did not become running")


def wait_until_batch_finishes(client: TestClient, batch_id: str) -> dict[str, object]:
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        response = client.get(f"/api/v1/automation-batches/{batch_id}")
        response.raise_for_status()
        body = response.json()
        if body["status"] in {"completed", "completed_with_errors", "failed"}:
            return body
        time.sleep(0.01)
    raise AssertionError("automation batch did not finish")


def wait_until_signup_finishes(client: TestClient, test_id: str) -> dict[str, object]:
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        response = client.get(f"/api/v1/signup-tests/{test_id}")
        response.raise_for_status()
        body = response.json()
        if body["status"] in {
            "completed",
            "captcha_required",
            "email_rejected",
            "cancelled",
            "failed",
        }:
            return body
        time.sleep(0.01)
    raise AssertionError("signup test did not finish")


def runtime_root(name: str) -> Path:
    root = Path(".test-runtime") / name
    shutil.rmtree(root, ignore_errors=True)
    root.mkdir(parents=True, exist_ok=True)
    return root


def test_complete_session_lifecycle_and_idempotency() -> None:
    root = runtime_root("lifecycle")
    settings = Settings(
        upload_root=root / "uploads",
        scheduler_workers=2,
        max_queued_jobs=8,
    )
    app = create_app(settings)

    with TestClient(app) as client:
        assert client.get("/health/live").json()["status"] == "ok"
        assert client.get("/health/ready").json()["runtime"] == "simulated"

        request = {
            "tenant_id": "tenant-a",
            "start_url": "https://example.com/start",
            "mode": "hidden",
            "priority": 75,
        }
        first = client.post(
            "/api/v1/sessions",
            json=request,
            headers={"Idempotency-Key": "create-session-001"},
        )
        assert first.status_code == 202
        session_id = first.json()["id"]

        duplicate = client.post(
            "/api/v1/sessions",
            json=request,
            headers={"Idempotency-Key": "create-session-001"},
        )
        assert duplicate.status_code == 202
        assert duplicate.json()["id"] == session_id

        running = wait_until_running(client, session_id)
        assert running["current_url"] == "https://example.com/start"

        navigated = client.post(
            f"/api/v1/sessions/{session_id}/navigate",
            json={"url": "https://example.com/next"},
        )
        assert navigated.status_code == 200
        assert navigated.json()["current_url"] == "https://example.com/next"

        uploaded = client.post(
            f"/api/v1/sessions/{session_id}/upload",
            files={"file": ("clip.mp4", b"video-bytes", "video/mp4")},
        )
        assert uploaded.status_code == 200
        assert uploaded.json() == {"filename": "clip.mp4", "bytes_received": 11}
        assert not list(settings.upload_root.iterdir())

        listing = client.get("/api/v1/sessions", params={"tenant_id": "tenant-a"})
        assert listing.status_code == 200
        assert listing.json()["total"] == 1

        closed = client.delete(f"/api/v1/sessions/{session_id}")
        assert closed.status_code == 200
        assert closed.json()["status"] == "closed"
    shutil.rmtree(root, ignore_errors=True)


def test_event_and_frame_websockets() -> None:
    root = runtime_root("websockets")
    settings = Settings(
        upload_root=root / "uploads",
        scheduler_workers=1,
        max_queued_jobs=4,
    )
    app = create_app(settings)

    with TestClient(app) as client:
        with client.websocket_connect("/ws/events") as events:
            created = client.post(
                "/api/v1/sessions",
                json={
                    "tenant_id": "tenant-a",
                    "start_url": "https://example.com",
                    "mode": "hidden",
                },
            )
            session_id = created.json()["id"]
            event = events.receive_json()
            assert event["type"] == "session.created"
            assert event["session_id"] == session_id

        wait_until_running(client, session_id)
        with client.websocket_connect(f"/ws/sessions/{session_id}/stream") as stream:
            frame = stream.receive_bytes()
            assert frame.startswith(b"\xff\xd8")
    shutil.rmtree(root, ignore_errors=True)


def test_upload_limit_returns_413_and_removes_partial_file() -> None:
    root = runtime_root("upload-limit")
    settings = Settings(
        upload_root=root / "uploads",
        scheduler_workers=1,
        max_queued_jobs=4,
        max_upload_bytes=4,
    )
    app = create_app(settings)

    with TestClient(app) as client:
        created = client.post(
            "/api/v1/sessions",
            json={"tenant_id": "tenant-a", "start_url": "https://example.com"},
        )
        session_id = created.json()["id"]
        wait_until_running(client, session_id)

        response = client.post(
            f"/api/v1/sessions/{session_id}/upload",
            files={"file": ("large.mp4", b"12345", "video/mp4")},
        )
        assert response.status_code == 413
        assert not list(settings.upload_root.iterdir())
    shutil.rmtree(root, ignore_errors=True)


def test_frontend_create_contract_accepts_initial_url_and_identity_fields() -> None:
    root = runtime_root("frontend-contract")
    settings = Settings(
        upload_root=root / "uploads",
        scheduler_workers=1,
        max_queued_jobs=4,
    )
    app = create_app(settings)

    with TestClient(app) as client:
        created = client.post(
            "/api/v1/sessions",
            json={
                "tenant_id": "tenant-a",
                "display_name": "Research session",
                "initial_url": "about:blank",
                "mode": "hidden",
                "locale": "auto",
                "timezone": "auto",
                "priority": 50,
            },
        )

        assert created.status_code == 202
        body = created.json()
        assert body["display_name"] == "Research session"
        assert body["initial_url"] == "about:blank"
        assert body["start_url"] == "about:blank"
        assert body["current_url"] == "about:blank"
        assert body["locale"] == "auto"
        assert body["timezone"] == "auto"
    shutil.rmtree(root, ignore_errors=True)


def test_blank_browser_is_a_single_visible_workspace() -> None:
    root = runtime_root("blank-singleton")
    settings = Settings(
        upload_root=root / "uploads",
        scheduler_workers=2,
        max_queued_jobs=8,
    )
    app = create_app(settings)

    with TestClient(app) as client:
        request = {
            "tenant_id": "blank-browser",
            "display_name": "TD trắng",
            "initial_url": "https://www.tiktok.com/tiktokstudio/upload?lang=en",
            "mode": "visible",
        }
        first = client.post("/api/v1/sessions", json=request)
        second = client.post("/api/v1/sessions", json=request)

        assert first.status_code == 202
        assert second.status_code == 202
        assert second.json()["id"] == first.json()["id"]
    shutil.rmtree(root, ignore_errors=True)


def test_ephemeral_batch_api_runs_in_parallel_and_auto_closes() -> None:
    root = runtime_root("ephemeral-batch")
    settings = Settings(
        upload_root=root / "uploads",
        scheduler_workers=2,
        max_queued_jobs=8,
        batch_max_jobs=10,
        batch_max_concurrency=4,
    )
    app = create_app(settings)

    with TestClient(app) as client:
        policy = client.get("/api/v1/automation-batches/policy")
        assert policy.status_code == 200
        assert policy.json() == {"max_jobs": 10, "max_concurrency": 4}

        created = client.post(
            "/api/v1/automation-batches",
            json={
                "tenant_id": "automation",
                "display_name": "API smoke",
                "start_url": "about:blank",
                "mode": "hidden",
                "total_jobs": 4,
                "concurrency": 2,
                "active_seconds": 0.1,
            },
        )
        assert created.status_code == 202
        finished = wait_until_batch_finishes(client, created.json()["id"])
        assert finished["status"] == "completed"
        assert finished["completed_jobs"] == 4

        sessions = client.get(
            "/api/v1/sessions", params={"tenant_id": "automation"}
        ).json()["items"]
        assert len(sessions) == 4
        assert all(item["ephemeral"] for item in sessions)
        assert all(item["status"] == "closed" for item in sessions)
        assert all(item["phase"] == "completed" for item in sessions)
    shutil.rmtree(root, ignore_errors=True)


def test_persistent_queue_api_supports_static_proxies_start_and_retry() -> None:
    root = runtime_root("persistent-queue-api")
    settings = Settings(
        upload_root=root / "uploads",
        scheduler_workers=2,
        max_queued_jobs=8,
        batch_max_jobs=10,
        batch_max_concurrency=4,
    )
    app = create_app(settings)

    with TestClient(app) as client:
        created = client.post(
            "/api/v1/automation-batches",
            json={
                "tenant_id": "automation",
                "display_name": "Manual queue",
                "start_url": "about:blank",
                "mode": "hidden",
                "total_jobs": 2,
                "concurrency": 2,
                "active_seconds": 0.1,
                "auto_start": False,
                "proxies": [
                    {"server": "http://proxy-one.example:8080"},
                    {"server": "socks5://proxy-two.example:1080"},
                ],
            },
        )
        assert created.status_code == 202
        queued = created.json()
        assert queued["status"] == "queued"
        assert queued["queue_status"] == "queued"
        assert queued["proxy_servers"] == [
            "http://proxy-one.example:8080",
            "socks5://proxy-two.example:1080",
        ]

        started = client.post(f"/api/v1/automation-batches/{queued['id']}/start")
        assert started.status_code == 200
        assert started.json()["queue_status"] == "running"
        finished = wait_until_batch_finishes(client, queued["id"])
        assert finished["queue_status"] == "succeeded"

        retried = client.post(f"/api/v1/automation-batches/{queued['id']}/retry")
        assert retried.status_code == 202
        assert retried.json()["id"] != queued["id"]
        retry_finished = wait_until_batch_finishes(client, retried.json()["id"])
        assert retry_finished["queue_status"] == "succeeded"
    shutil.rmtree(root, ignore_errors=True)


def test_sqlite_queue_history_survives_restart_and_can_retry_safe_work() -> None:
    root = runtime_root("sqlite-queue-restart")
    settings = Settings(
        upload_root=root / "uploads",
        database_path=root / "control-plane.sqlite3",
        scheduler_workers=1,
        max_queued_jobs=4,
        batch_max_jobs=4,
        batch_max_concurrency=2,
    )

    first_app = create_app(settings)
    with TestClient(first_app) as client:
        created = client.post(
            "/api/v1/automation-batches",
            json={
                "tenant_id": "automation",
                "display_name": "Restart-safe queue",
                "start_url": "about:blank",
                "mode": "hidden",
                "total_jobs": 1,
                "concurrency": 1,
                "active_seconds": 0.1,
                "auto_start": False,
            },
        ).json()
        assert created["queue_status"] == "queued"

    second_app = create_app(settings)
    with TestClient(second_app) as client:
        history = client.get("/api/v1/automation-batches").json()["items"]
        recovered = next(item for item in history if item["id"] == created["id"])
        assert recovered["queue_status"] == "failed"

        retried = client.post(f"/api/v1/automation-batches/{created['id']}/retry")
        assert retried.status_code == 202
        finished = wait_until_batch_finishes(client, retried.json()["id"])
        assert finished["queue_status"] == "succeeded"
    shutil.rmtree(root, ignore_errors=True)


def test_one_shot_signup_api_keeps_secrets_out_of_responses() -> None:
    root = runtime_root("signup-test")
    settings = Settings(
        upload_root=root / "uploads",
        scheduler_workers=1,
        max_queued_jobs=4,
    )
    app = create_app(settings)
    payload = {
        "start_url": "https://www.tiktok.com/tiktokstudio/upload?lang=en",
        "email": "owned-test@example.com",
        "account_password": "not-returned-password",
        "refresh_token": "not-returned-refresh-token",
        "client_id": "not-returned-client-id",
        "username": "owned_test_01",
        "birth_date": "2000-01-01",
        "proxy": {
            "server": "socks5://proxy.example:1080",
            "username": "proxy-user",
            "password": "not-returned-proxy-password",
        },
        "fallback_mailboxes": [
            {
                "email": "owned-fallback@example.com",
                "refresh_token": "not-returned-fallback-refresh-token",
                "client_id": "not-returned-fallback-client-id",
            }
        ],
    }

    with TestClient(app) as client:
        created = client.post("/api/v1/signup-tests", json=payload)
        assert created.status_code == 202
        test_id = created.json()["id"]
        finished = wait_until_signup_finishes(client, test_id)
        assert finished["status"] == "completed"
        assert finished["email_masked"] == "ow********@example.com"
        serialized = str(finished)
        assert payload["account_password"] not in serialized
        assert payload["refresh_token"] not in serialized
        assert payload["client_id"] not in serialized
        assert payload["proxy"]["password"] not in serialized
        assert payload["fallback_mailboxes"][0]["refresh_token"] not in serialized
        assert payload["fallback_mailboxes"][0]["client_id"] not in serialized
        assert finished["email_attempts"] == 1
        assert finished["total_email_candidates"] == 2

        session = client.get(
            f"/api/v1/sessions/{finished['session_id']}"
        ).json()
        assert session["status"] == "closed"
        assert session["extensions_enabled"] is True
        assert session["humanize"] is True
        assert session["proxy_server"] == "socks5://proxy.example:1080"

        duplicate = client.post("/api/v1/signup-tests", json=payload)
        assert duplicate.status_code == 400
        assert duplicate.json()["code"] == "signup_test_already_consumed"
    shutil.rmtree(root, ignore_errors=True)
