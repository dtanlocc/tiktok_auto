from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from invisible_browser_studio.application.errors import (
    ApplicationError,
    BatchNotFound,
    CapacityExceeded,
    RuntimeOperationFailed,
    SessionNotFound,
    SignupTestNotFound,
)
from invisible_browser_studio.domain.errors import DomainError

from invisible_browser_studio.adapters.inbound.http import api, health, ws
from invisible_browser_studio.infrastructure.config import Settings
from invisible_browser_studio.infrastructure.container import Container


def create_app(
    settings: Settings | None = None, container: Container | None = None
) -> FastAPI:
    resolved_settings = settings or Settings.from_env()
    resolved_container = container or Container.build(resolved_settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        resolved_settings.upload_root.mkdir(parents=True, exist_ok=True)
        await resolved_container.batches.initialize()
        await resolved_container.accounts.initialize()
        await resolved_container.scheduler.start()
        try:
            yield
        finally:
            await resolved_container.signup_tests.shutdown()
            await resolved_container.batches.shutdown()
            await resolved_container.accounts.shutdown()
            await resolved_container.batch_repository.close()
            await resolved_container.scheduler.stop()
            await resolved_container.runtime.shutdown()

    app = FastAPI(
        title=resolved_settings.app_name,
        version="0.1.0",
        description="Session orchestration API for isolated browser workers.",
        lifespan=lifespan,
    )
    app.state.container = resolved_container
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(resolved_settings.cors_origins),
        allow_credentials=False,
        allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "Idempotency-Key"],
    )

    @app.exception_handler(SessionNotFound)
    async def not_found_handler(_: Request, exc: SessionNotFound) -> JSONResponse:
        return JSONResponse(
            status_code=404, content={"code": exc.code, "message": str(exc)}
        )

    @app.exception_handler(BatchNotFound)
    async def batch_not_found_handler(_: Request, exc: BatchNotFound) -> JSONResponse:
        return JSONResponse(
            status_code=404, content={"code": exc.code, "message": str(exc)}
        )

    @app.exception_handler(SignupTestNotFound)
    async def signup_test_not_found_handler(
        _: Request, exc: SignupTestNotFound
    ) -> JSONResponse:
        return JSONResponse(
            status_code=404, content={"code": exc.code, "message": str(exc)}
        )

    @app.exception_handler(CapacityExceeded)
    async def capacity_handler(_: Request, exc: CapacityExceeded) -> JSONResponse:
        return JSONResponse(
            status_code=429,
            content={"code": exc.code, "message": str(exc)},
            headers={"Retry-After": "5"},
        )

    @app.exception_handler(RuntimeOperationFailed)
    async def runtime_handler(_: Request, exc: RuntimeOperationFailed) -> JSONResponse:
        return JSONResponse(
            status_code=502, content={"code": exc.code, "message": str(exc)}
        )

    @app.exception_handler(DomainError)
    async def domain_handler(_: Request, exc: DomainError) -> JSONResponse:
        return JSONResponse(
            status_code=409,
            content={"code": "domain_invariant", "message": str(exc)},
        )

    @app.exception_handler(ApplicationError)
    async def application_handler(_: Request, exc: ApplicationError) -> JSONResponse:
        return JSONResponse(
            status_code=400, content={"code": exc.code, "message": str(exc)}
        )

    app.include_router(health)
    app.include_router(api)
    app.include_router(ws)
    return app


app = create_app()
