"""Request plumbing: request ids, response headers, body size limit, last-resort errors."""

from __future__ import annotations

import json
import logging
import re
import time
import uuid

from starlette.datastructures import Headers, MutableHeaders
from starlette.exceptions import HTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from ..platform import apikeys

log = logging.getLogger(__name__)

MAX_BODY_BYTES = 1_000_000
_REQUEST_ID = re.compile(r"^[A-Za-z0-9-]{8,64}$")


def error_body(code: str, message: str, request_id: str | None = None, **extra) -> dict:
    """The one error shape every endpoint returns."""
    error = {"code": code, "message": message, **extra}
    if request_id:
        error["request_id"] = request_id
    return {"error": error}


class RequestContext:
    """Pure ASGI, so streaming responses (the live alert feed) pass through untouched."""

    def __init__(self, app: ASGIApp, production: bool = False) -> None:
        self.app = app
        self.production = production

    @staticmethod
    def _count_failure(scope: Scope, status: int) -> None:
        """A partner's failed request is counted against its key, for the usage chart."""
        key_id = scope.get("state", {}).get("api_key_id")
        if key_id is None or status < 400:
            return
        try:
            apikeys.count_error(scope["app"].state.platform.redis, key_id)
        except Exception:
            log.exception("could not count a failed request for key %s", key_id)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = Headers(scope=scope)
        request_id = headers.get("x-request-id", "")
        if not _REQUEST_ID.fullmatch(request_id):  # never echo arbitrary text into logs
            request_id = uuid.uuid4().hex
        scope.setdefault("state", {})["request_id"] = request_id
        started = time.perf_counter()
        response_started = False

        async def send_with_headers(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
                self._count_failure(scope, message["status"])
                out = MutableHeaders(scope=message)
                out["X-Request-ID"] = request_id
                out["X-Content-Type-Options"] = "nosniff"
                out["X-Frame-Options"] = "DENY"
                out["Referrer-Policy"] = "no-referrer"
                out["Server-Timing"] = f"app;dur={(time.perf_counter() - started) * 1000:.1f}"
                # Responses describe customers and investigations: nothing may keep a copy.
                out.setdefault("Cache-Control", "no-store")
                if self.production:
                    out["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
            await send(message)

        async def reply(status: int, code: str, message: str) -> None:
            body = json.dumps(error_body(code, message, request_id)).encode()
            await send_with_headers(
                {
                    "type": "http.response.start",
                    "status": status,
                    "headers": [
                        (b"content-type", b"application/json"),
                        (b"content-length", str(len(body)).encode()),
                    ],
                }
            )
            await send_with_headers({"type": "http.response.body", "body": body})

        too_large = f"the request body is limited to {MAX_BODY_BYTES} bytes"
        declared = headers.get("content-length")
        if declared is not None and (not declared.isdigit() or int(declared) > MAX_BODY_BYTES):
            await reply(413, "body_too_large", too_large)
            return
        received = 0

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > MAX_BODY_BYTES:  # a chunked body with no declared length
                    raise HTTPException(413, too_large)
            return message

        try:
            await self.app(scope, limited_receive, send_with_headers)
        except Exception:
            log.exception(
                "unhandled error on %s %s [%s]", scope["method"], scope["path"], request_id
            )
            if response_started:
                raise
            # Nothing about the failure goes to the caller except the id to quote.
            await reply(500, "internal_error", "something went wrong on our side")
