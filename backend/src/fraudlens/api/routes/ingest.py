"""The signed webhook a core-banking system pushes transactions to (docs/INGEST.md).

No bearer token: the partner signs each request with its HMAC key. The raw body is
read before anything is parsed, because the signature is over the bytes sent.
"""

from __future__ import annotations

import time
from typing import Literal

from fastapi import APIRouter, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool

from ...platform import callbacks
from ...platform.audit import WorkflowError
from ...platform.ingest import (
    IDEMPOTENCY_HEADER,
    KEY_HEADER,
    NONCE_HEADER,
    SIGNATURE_HEADER,
    TIMESTAMP_HEADER,
    Idempotency,
    IngestInvalid,
    Pacs008,
    claim_nonce,
    first_sightings,
    forget_sightings,
    parse,
    verify,
)
from ...platform.keys import Key, load_keyring
from ...platform.stream import MAX_BACKLOG, enqueue
from ..deps import Platform
from ..views import result_view

router = APIRouter(prefix="/ingest", tags=["ingest"])
_ERROR = "__error__"  # a stored refusal, replayed as the same error

_SIGNED = {
    "parameters": [
        {
            "name": h,
            "in": "header",
            "required": h != IDEMPOTENCY_HEADER,
            "schema": {"type": "string"},
        }
        for h in (KEY_HEADER, TIMESTAMP_HEADER, NONCE_HEADER, SIGNATURE_HEADER, IDEMPOTENCY_HEADER)
    ],
    "requestBody": {
        "required": True,
        "content": {"application/json": {"schema": Pacs008.model_json_schema()}},
    },
}


def _authenticate(p: Platform, request: Request, body: bytes) -> Key:
    if p.settings.ingest_keyring is None:
        raise WorkflowError(404, "not_found", "ingest is not configured on this service")
    path = request.url.path + (f"?{request.url.query}" if request.url.query else "")
    window = p.settings.ingest_window_seconds
    key = verify(
        load_keyring(p.settings.ingest_keyring), request.headers, request.method, path, body, window
    )
    claim_nonce(p.redis, key, request.headers[NONCE_HEADER], window)
    return key


def _parse(body: bytes, single: bool):
    try:
        return parse(body, single)
    except ValidationError as exc:
        raise RequestValidationError(exc.errors()) from None
    except IngestInvalid as exc:
        raise RequestValidationError(exc.errors) from None


def _queue(p: Platform, key: Key, message: Pacs008, txns: list) -> dict:
    backlog = p.worker.backlog()
    if backlog > MAX_BACKLOG:
        raise WorkflowError(
            503, "backlog_full", "the event queue is full; retry later",
            retry_after_seconds=30, backlog=backlog,
        )  # fmt: skip
    ids = [t.txn_id for t in txns]
    new = first_sightings(p.redis, ids)
    fresh = [t for t, n in zip(txns, new, strict=True) if n]
    try:
        enqueue(p.redis, p.settings.events_stream, fresh)
    except Exception:
        forget_sightings(p.redis, [t.txn_id for t in fresh])
        raise
    ring = load_keyring(p.settings.ingest_keyring)
    if ring.callback_url(key.partner):
        now = time.time()
        for transfer, is_new in zip(message.CdtTrfTxInf, new, strict=True):
            if is_new:
                callbacks.schedule(
                    p.redis, key.partner, int(transfer.PmtId.TxId), transfer.PmtId.EndToEndId, now
                )
    return {
        "msg_id": message.GrpHdr.MsgId,
        "accepted": len(fresh),
        "duplicates": [
            t.PmtId.EndToEndId for t, n in zip(message.CdtTrfTxInf, new, strict=True) if not n
        ],
        "callback": bool(ring.callback_url(key.partner)),
    }


def _decide(p: Platform, message: Pacs008, txns: list) -> dict:
    [new] = first_sightings(p.redis, [txns[0].txn_id])  # a later queued copy is a duplicate
    try:
        [result] = p.scorer.process([txns[0].event()])
    except Exception:
        if new:
            forget_sightings(p.redis, [txns[0].txn_id])
        raise
    if result.status == "stale":
        raise WorkflowError(
            409, "stale_event", "this transaction is too far behind the stream to be scored"
        )
    return {"end_to_end_id": message.CdtTrfTxInf[0].PmtId.EndToEndId, **result_view(result)}


async def _handle(request: Request, single: bool, wait: bool) -> JSONResponse:
    p: Platform = request.app.state.platform
    body = await request.body()

    def work() -> tuple[int, dict, bool]:
        key = _authenticate(p, request, body)
        idem = Idempotency(p.redis, key.partner, request.headers.get(IDEMPOTENCY_HEADER), body)
        replay = idem.begin()
        if replay is not None:
            return *replay, True
        try:
            message, txns = _parse(body, single)
            if wait:
                status, answer = 200, jsonable_encoder(_decide(p, message, txns))
            else:
                status, answer = 202, _queue(p, key, message, txns)
        except WorkflowError as exc:
            if exc.status >= 500:
                idem.abandon()
            else:  # a refusal is an answer too: the same request gets it again
                idem.finish(exc.status, {_ERROR: [exc.status, exc.code, exc.message]})
            raise
        except Exception:  # invalid, or a backing service failed: a retry may run it again
            idem.abandon()
            raise
        idem.finish(status, answer)
        return status, answer, False

    status, answer, replayed = await run_in_threadpool(work)
    if _ERROR in answer:
        code_status, code, message = answer[_ERROR]
        raise WorkflowError(code_status, code, message)
    headers = {"Idempotent-Replayed": "true"} if replayed else {}
    return JSONResponse(answer, status, headers=headers)


@router.post("/transactions", status_code=202, openapi_extra=_SIGNED)
async def ingest_one(
    request: Request,
    wait: Literal["decision"] | None = Query(
        default=None, description="`decision`: score now and answer with it (200)"
    ),
) -> JSONResponse:
    """One pacs.008 credit transfer, HMAC-signed by a partner. Queued for the stream
    (202) or, with `?wait=decision`, decided now (200, the same answer as /v1/score)."""
    return await _handle(request, single=True, wait=wait == "decision")


@router.post("/transactions/batch", status_code=202, openapi_extra=_SIGNED)
async def ingest_batch(request: Request) -> JSONResponse:
    """Up to 500 credit transfers in one signed pacs.008 message, queued in order."""
    return await _handle(request, single=False, wait=False)
