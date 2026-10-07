"""The application: what is built at start-up, and how failures are reported."""

from __future__ import annotations

import logging
import os
import threading
from contextlib import asynccontextmanager

import redis.asyncio as aioredis
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from redis import Redis
from redis.exceptions import RedisError
from sqlalchemy.exc import InterfaceError, OperationalError
from starlette.exceptions import HTTPException

from ..config import Settings
from ..intel.text import load_text_model
from ..platform.audit import WorkflowError
from ..platform.callbacks import Dispatcher
from ..platform.db import make_engine, make_sessions
from ..platform.graph import Graph
from ..platform.scoring import Scorer
from ..platform.security import RateLimiter, check_production
from ..platform.stream import Worker
from .deps import Platform
from .middleware import RequestContext, error_body
from .routes import (
    alerts,
    appeals,
    auth,
    cases,
    consortium,
    customer,
    demo,
    ingest,
    intel,
    mlops,
    network,
    ops,
    refunds,
    scoring,
)

log = logging.getLogger(__name__)

DESCRIPTION = """Scam-to-cash-out interception for mobile money.

The scoring endpoints are what a payment switch calls; the rest serves the
analyst console. Every consequential action (releasing or blocking held money,
freezing a wallet) is taken by a named person and written to the audit log.
All data served here is synthetic."""

_HTTP_CODES = {
    401: "unauthenticated",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    413: "body_too_large",
}


def build_platform(settings: Settings) -> Platform:
    check_production(settings)
    db = make_engine(settings.database_url)
    sessions = make_sessions(db)
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    scorer = Scorer(settings, sessions, redis)
    narrator = None
    if settings.llm_notes and os.environ.get("ANTHROPIC_API_KEY"):
        from ..decision.narrative import LLMNarrator

        narrator = LLMNarrator()
    return Platform(
        settings=settings,
        db=db,
        sessions=sessions,
        redis=redis,
        scorer=scorer,
        graph=Graph(scorer),
        worker=Worker(scorer, redis, settings),
        narrator=narrator,
        login_limit=RateLimiter(
            redis, "login", settings.login_max_attempts, settings.login_window_seconds
        ),
        # One address trying many usernames is stopped too, at a looser limit.
        login_ip_limit=RateLimiter(
            redis, "login-ip", settings.login_max_attempts * 10, settings.login_window_seconds
        ),
        check_limit=RateLimiter(redis, "recipient-check", 30, 60),
        report_limit=RateLimiter(redis, "report", 5, 3600),
        intel=load_text_model(settings),
        message_limit=RateLimiter(redis, "message-check", 20, 60),
        # Tighter: each call is a question about the ledger.
        proof_limit=RateLimiter(redis, "payment-verify", 10, 60),
        callbacks=Dispatcher(settings, sessions, redis) if settings.ingest_keyring else None,
    )


def _request_id(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        platform = build_platform(settings)
        app.state.platform = platform
        app.state.aredis = aioredis.from_url(settings.redis_url, decode_responses=True)
        if settings.run_worker:
            platform.worker.start()
            if platform.callbacks is not None:
                platform.callbacks.start()
        # Stream workers in other processes change the state too: follow the log.
        following = threading.Event()
        if settings.follow_interval_s > 0:
            threading.Thread(
                target=platform.scorer.follow,
                args=(following, settings.follow_interval_s),
                name="fraudlens-follower",
                daemon=True,
            ).start()
        log.info("fraudlens api ready: scoring in %s mode", platform.scorer.mode)
        try:
            yield
        finally:
            if platform.callbacks is not None:
                platform.callbacks.stop()
            following.set()
            platform.worker.stop()
            if platform.worker.is_alive():
                platform.worker.join(timeout=5.0)
            await app.state.aredis.aclose()
            platform.redis.close()
            platform.db.dispose()

    app = FastAPI(
        title="FraudLens API",
        version="1.0.0",
        description=DESCRIPTION,
        lifespan=lifespan,
        # The interactive docs are a development aid, not part of the service.
        docs_url=None if settings.production else "/docs",
        redoc_url=None,
        openapi_url=None if settings.production else "/openapi.json",
    )
    app.add_middleware(RequestContext, production=settings.production)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["GET", "POST"],
        allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
        expose_headers=["X-Request-ID", "Retry-After"],
        allow_credentials=False,  # bearer tokens, no cookies
    )

    @app.exception_handler(WorkflowError)
    async def workflow_error(request: Request, exc: WorkflowError) -> JSONResponse:
        headers = {}
        if "retry_after_seconds" in exc.extra:
            headers["Retry-After"] = str(exc.extra["retry_after_seconds"])
        if exc.status == 401:
            headers["WWW-Authenticate"] = "Bearer"
        body = error_body(exc.code, exc.message, _request_id(request), **exc.extra)
        return JSONResponse(body, exc.status, headers=headers)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, exc: RequestValidationError) -> JSONResponse:
        # Where and what, but never the rejected value itself: it may be a password.
        fields = [
            {"field": ".".join(str(part) for part in e["loc"]), "problem": e["msg"]}
            for e in exc.errors()
        ]
        body = error_body(
            "invalid_request", "the request is not valid", _request_id(request), fields=fields
        )
        return JSONResponse(body, 422)

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException) -> JSONResponse:
        code = _HTTP_CODES.get(exc.status_code, "error")
        body = error_body(code, str(exc.detail), _request_id(request))
        return JSONResponse(body, exc.status_code, headers=exc.headers)

    async def unavailable(request: Request, exc: Exception) -> JSONResponse:
        log.error("dependency unavailable [%s]: %s", _request_id(request), type(exc).__name__)
        body = error_body(
            "dependency_unavailable", "a backing service is unavailable", _request_id(request)
        )
        return JSONResponse(body, 503, headers={"Retry-After": "5"})

    for kind in (OperationalError, InterfaceError, RedisError):
        app.add_exception_handler(kind, unavailable)

    for module in (
        auth,
        scoring,
        ingest,
        alerts,
        cases,
        appeals,
        refunds,
        network,
        customer,
        intel,
        ops,
        mlops,
        consortium,
    ):
        app.include_router(module.router, prefix="/v1")
    if not settings.production:  # stand-ins for the customer app; see routes/demo.py
        app.include_router(demo.router, prefix="/v1")
    app.include_router(ops.health)
    return app
