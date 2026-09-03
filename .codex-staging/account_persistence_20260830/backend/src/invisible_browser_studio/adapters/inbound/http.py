from __future__ import annotations

import asyncio
import re
import uuid
from pathlib import Path
from typing import Annotated

from fastapi import (
    APIRouter,
    File,
    Header,
    HTTPException,
    Query,
    Request,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
    status,
)

from invisible_browser_studio.application.dto import (
    CreateAutomationBatchCommand,
    CreateSessionCommand,
    CreateSignupTestCommand,
    ImportAccountCommand,
    SignupMailbox,
)
from invisible_browser_studio.domain import ProxyConfig
from invisible_browser_studio.infrastructure.container import Container

from .schemas import (
    AccountImportRequest,
    AccountImportResponse,
    AccountListResponse,
    AccountResponse,
    AutomationBatchListResponse,
    AutomationBatchPolicyResponse,
    AutomationBatchResponse,
    CreateAutomationBatchRequest,
    CreateSessionRequest,
    CreateSignupTestRequest,
    EventMessage,
    HealthResponse,
    NavigateRequest,
    SessionListResponse,
    SessionResponse,
    SignupTestResponse,
    UploadResponse,
)

api = APIRouter(prefix="/api/v1")
health = APIRouter(prefix="/health")
ws = APIRouter(prefix="/ws")

_SAFE_FILENAME = re.compile(r"[^A-Za-z0-9._ -]+")
_UPLOAD_CHUNK_BYTES = 1024 * 1024


def _container(request: Request) -> Container:
    return request.app.state.container


@health.get("/live", response_model=HealthResponse, tags=["health"])
async def live(request: Request) -> HealthResponse:
    container = _container(request)
    return HealthResponse(status="ok", service=container.settings.app_name)


@health.get("/ready", response_model=HealthResponse, tags=["health"])
async def ready(request: Request) -> HealthResponse:
    container = _container(request)
    return HealthResponse(
        status="ready",
        service=container.settings.app_name,
        runtime=container.settings.runtime,
    )


@api.post(
    "/sessions",
    response_model=SessionResponse,
    status_code=status.HTTP_202_ACCEPTED,
    tags=["sessions"],
)
async def create_session(
    body: CreateSessionRequest,
    request: Request,
    idempotency_key: Annotated[
        str | None, Header(alias="Idempotency-Key", min_length=8, max_length=128)
    ] = None,
) -> SessionResponse:
    proxy = None
    if body.proxy:
        proxy = ProxyConfig(
            server=body.proxy.server,
            username=body.proxy.username,
            password=body.proxy.password.get_secret_value() if body.proxy.password else None,
        )
    session = await _container(request).sessions.create(
        CreateSessionCommand(
            tenant_id=body.tenant_id,
            start_url=body.start_url,
            display_name=body.display_name,
            mode=body.mode,
            locale=body.locale,
            timezone=body.timezone,
            proxy=proxy,
            priority=body.priority,
            idempotency_key=idempotency_key,
        )
    )
    return SessionResponse.from_domain(session)


@api.get("/sessions", response_model=SessionListResponse, tags=["sessions"])
async def list_sessions(
    request: Request,
    tenant_id: Annotated[str | None, Query(min_length=1, max_length=128)] = None,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> SessionListResponse:
    sessions, total = await _container(request).sessions.list(
        tenant_id=tenant_id, offset=offset, limit=limit
    )
    return SessionListResponse(
        items=[SessionResponse.from_domain(item) for item in sessions],
        total=total,
        offset=offset,
        limit=limit,
    )


@api.post(
    "/accounts/import",
    response_model=AccountImportResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["accounts"],
)
async def import_accounts(
    body: AccountImportRequest, request: Request
) -> AccountImportResponse:
    result = await _container(request).accounts.import_many(
        tuple(
            ImportAccountCommand(
                email=row.email,
                email_password=row.email_password.get_secret_value(),
                refresh_token=row.refresh_token.get_secret_value(),
                client_id=row.client_id.get_secret_value(),
                source_name=row.source_name,
            )
            for row in body.rows
        )
    )
    return AccountImportResponse(
        imported=result.imported,
        duplicates=result.duplicates,
        total=result.total,
    )


@api.get("/accounts", response_model=AccountListResponse, tags=["accounts"])
async def list_accounts(
    request: Request,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=1000)] = 500,
) -> AccountListResponse:
    accounts, total = await _container(request).accounts.list(
        offset=offset,
        limit=limit,
    )
    return AccountListResponse(
        items=[AccountResponse.from_domain(account) for account in accounts],
        total=total,
        offset=offset,
        limit=limit,
    )


@api.get(
    "/automation-batches/policy",
    response_model=AutomationBatchPolicyResponse,
    tags=["automation-batches"],
)
async def automation_batch_policy(request: Request) -> AutomationBatchPolicyResponse:
    batches = _container(request).batches
    return AutomationBatchPolicyResponse(
        max_jobs=batches.max_jobs,
        max_concurrency=batches.max_concurrency,
    )


@api.post(
    "/automation-batches",
    response_model=AutomationBatchResponse,
    status_code=status.HTTP_202_ACCEPTED,
    tags=["automation-batches"],
)
async def create_automation_batch(
    body: CreateAutomationBatchRequest, request: Request
) -> AutomationBatchResponse:
    proxy = None
    if body.proxy:
        proxy = ProxyConfig(
            server=body.proxy.server,
            username=body.proxy.username,
            password=body.proxy.password.get_secret_value() if body.proxy.password else None,
        )
    proxies = tuple(
        ProxyConfig(
            server=item.server,
            username=item.username,
            password=item.password.get_secret_value() if item.password else None,
        )
        for item in body.proxies
    )
    batches = _container(request).batches
    batch = await batches.create(
        CreateAutomationBatchCommand(
            tenant_id=body.tenant_id,
            display_name=body.display_name,
            start_url=body.start_url,
            mode=body.mode,
            total_jobs=body.total_jobs,
            concurrency=body.concurrency,
            active_seconds=body.active_seconds,
            locale=body.locale,
            timezone=body.timezone,
            proxy=proxy,
            proxies=proxies,
            rotation_url=(body.rotation_url.get_secret_value() if body.rotation_url else None),
            priority=body.priority,
        )
    )
    if body.auto_start:
        batch = await batches.start(batch.id)
    return AutomationBatchResponse.from_domain(batch)


@api.post(
    "/automation-batches/{batch_id}/start",
    response_model=AutomationBatchResponse,
    tags=["automation-batches"],
)
async def start_automation_batch(batch_id: str, request: Request) -> AutomationBatchResponse:
    batch = await _container(request).batches.start(batch_id)
    return AutomationBatchResponse.from_domain(batch)


@api.post(
    "/automation-batches/{batch_id}/retry",
    response_model=AutomationBatchResponse,
    status_code=status.HTTP_202_ACCEPTED,
    tags=["automation-batches"],
)
async def retry_automation_batch(batch_id: str, request: Request) -> AutomationBatchResponse:
    batch = await _container(request).batches.retry(batch_id)
    return AutomationBatchResponse.from_domain(batch)


@api.get(
    "/automation-batches",
    response_model=AutomationBatchListResponse,
    tags=["automation-batches"],
)
async def list_automation_batches(
    request: Request,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> AutomationBatchListResponse:
    batches, total = await _container(request).batches.list(offset=offset, limit=limit)
    return AutomationBatchListResponse(
        items=[AutomationBatchResponse.from_domain(item) for item in batches],
        total=total,
        offset=offset,
        limit=limit,
    )


@api.get(
    "/automation-batches/{batch_id}",
    response_model=AutomationBatchResponse,
    tags=["automation-batches"],
)
async def get_automation_batch(batch_id: str, request: Request) -> AutomationBatchResponse:
    batch = await _container(request).batches.get(batch_id)
    return AutomationBatchResponse.from_domain(batch)


@api.delete(
    "/automation-batches/{batch_id}",
    response_model=AutomationBatchResponse,
    tags=["automation-batches"],
)
async def cancel_automation_batch(batch_id: str, request: Request) -> AutomationBatchResponse:
    batch = await _container(request).batches.cancel(batch_id)
    return AutomationBatchResponse.from_domain(batch)


@api.post(
    "/signup-tests",
    response_model=SignupTestResponse,
    status_code=status.HTTP_202_ACCEPTED,
    tags=["signup-test"],
)
async def create_signup_test(
    body: CreateSignupTestRequest, request: Request
) -> SignupTestResponse:
    proxy = None
    if body.proxy:
        proxy = ProxyConfig(
            server=body.proxy.server,
            username=body.proxy.username,
            password=(
                body.proxy.password.get_secret_value()
                if body.proxy.password
                else None
            ),
        )
    test = await _container(request).signup_tests.create(
        CreateSignupTestCommand(
            start_url=body.start_url,
            email=body.email,
            account_password=body.account_password.get_secret_value(),
            refresh_token=body.refresh_token.get_secret_value(),
            client_id=body.client_id.get_secret_value(),
            username=body.username,
            birth_date=body.birth_date,
            proxy=proxy,
            fallback_mailboxes=tuple(
                SignupMailbox(
                    email=mailbox.email,
                    refresh_token=mailbox.refresh_token.get_secret_value(),
                    client_id=mailbox.client_id.get_secret_value(),
                )
                for mailbox in body.fallback_mailboxes
            ),
        )
    )
    return SignupTestResponse.from_domain(test)


@api.get(
    "/signup-tests/current",
    response_model=SignupTestResponse,
    tags=["signup-test"],
)
async def current_signup_test(request: Request) -> SignupTestResponse:
    test = await _container(request).signup_tests.get()
    return SignupTestResponse.from_domain(test)


@api.get(
    "/signup-tests/{test_id}",
    response_model=SignupTestResponse,
    tags=["signup-test"],
)
async def get_signup_test(test_id: str, request: Request) -> SignupTestResponse:
    test = await _container(request).signup_tests.get(test_id)
    return SignupTestResponse.from_domain(test)


@api.delete(
    "/signup-tests/{test_id}",
    response_model=SignupTestResponse,
    tags=["signup-test"],
)
async def cancel_signup_test(test_id: str, request: Request) -> SignupTestResponse:
    test = await _container(request).signup_tests.cancel(test_id)
    return SignupTestResponse.from_domain(test)


@api.get("/sessions/{session_id}", response_model=SessionResponse, tags=["sessions"])
async def get_session(session_id: str, request: Request) -> SessionResponse:
    session = await _container(request).sessions.get(session_id)
    return SessionResponse.from_domain(session)


@api.post(
    "/sessions/{session_id}/navigate",
    response_model=SessionResponse,
    tags=["sessions"],
)
async def navigate_session(
    session_id: str, body: NavigateRequest, request: Request
) -> SessionResponse:
    session = await _container(request).sessions.navigate(session_id, str(body.url))
    return SessionResponse.from_domain(session)


@api.post(
    "/sessions/{session_id}/upload",
    response_model=UploadResponse,
    tags=["sessions"],
)
async def upload_file(
    session_id: str,
    request: Request,
    file: Annotated[UploadFile, File(description="File streamed to the browser session")],
) -> UploadResponse:
    container = _container(request)
    original_name = Path(file.filename or "upload.bin").name
    safe_name = _SAFE_FILENAME.sub("_", original_name).strip(" .") or "upload.bin"
    destination = container.settings.upload_root / f"{uuid.uuid4()}_{safe_name}"
    destination.parent.mkdir(parents=True, exist_ok=True)
    size = 0
    try:
        with destination.open("xb") as output:
            while chunk := await file.read(_UPLOAD_CHUNK_BYTES):
                size += len(chunk)
                if size > container.settings.max_upload_bytes:
                    raise HTTPException(
                        status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                        detail={
                            "code": "upload_too_large",
                            "message": "upload exceeds configured byte limit",
                        },
                    )
                await asyncio.to_thread(output.write, chunk)
        result = await container.sessions.upload(
            session_id,
            destination,
            original_filename=original_name,
            size=size,
        )
        return UploadResponse(filename=result.filename, bytes_received=result.bytes_received)
    finally:
        await file.close()
        try:
            destination.unlink(missing_ok=True)
        except OSError:
            pass


@api.post(
    "/sessions/{session_id}/close",
    response_model=SessionResponse,
    tags=["sessions"],
)
@api.delete(
    "/sessions/{session_id}",
    response_model=SessionResponse,
    tags=["sessions"],
)
async def close_session(session_id: str, request: Request) -> SessionResponse:
    session = await _container(request).sessions.close(session_id)
    await _container(request).frames.discard(session_id)
    return SessionResponse.from_domain(session)


@ws.websocket("/events")
async def event_stream(websocket: WebSocket) -> None:
    await websocket.accept()
    container: Container = websocket.app.state.container
    try:
        async with container.events.subscribe() as queue:
            while True:
                event = await queue.get()
                message = EventMessage(
                    type=event.type,
                    session_id=event.session_id,
                    tenant_id=event.tenant_id,
                    payload=event.payload,
                    occurred_at=event.occurred_at,
                )
                await websocket.send_json(message.model_dump(mode="json"))
    except WebSocketDisconnect:
        return


@ws.websocket("/sessions/{session_id}/stream")
async def frame_stream(session_id: str, websocket: WebSocket) -> None:
    container: Container = websocket.app.state.container
    try:
        await container.sessions.get(session_id)
        await websocket.accept()
        async with container.frames.subscribe(session_id) as queue:
            while True:
                await websocket.send_bytes(await queue.get())
    except WebSocketDisconnect:
        return
    except Exception:
        if websocket.client_state.name != "DISCONNECTED":
            await websocket.close(code=4404, reason="Session not found")
