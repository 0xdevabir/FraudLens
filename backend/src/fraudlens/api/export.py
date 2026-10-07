"""CSV downloads of the alert queue and the case list.

An export moves data out of the console, so it is limited, masked and audited:
identifiers are masked unless a supervisor asks for them, and every export
writes who took it, with which filters, and how many rows.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Iterable, Iterator, Sequence
from typing import Any

from fastapi.responses import StreamingResponse

from ..platform.audit import WorkflowError

MAX_ROWS = 50_000
_FORMULA = ("=", "+", "-", "@", "\t", "\r")


def _cell(value: Any) -> Any:
    """A value for a spreadsheet: text starting like a formula is made inert."""
    if isinstance(value, str) and value.startswith(_FORMULA):
        return "'" + value
    return value


def _lines(columns: Sequence[str], rows: Iterable[Sequence[Any]]) -> Iterator[str]:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(columns)
    for row in rows:
        writer.writerow([_cell(v) for v in row])
        if buffer.tell() > 65_536:
            yield buffer.getvalue()
            buffer.seek(0)
            buffer.truncate()
    yield buffer.getvalue()


def csv_response(
    name: str, columns: Sequence[str], rows: Sequence[Sequence[Any]], total: int
) -> StreamingResponse:
    headers = {
        "Content-Disposition": f'attachment; filename="{name}.csv"',
        "X-Total-Rows": str(total),
        "X-Exported-Rows": str(len(rows)),
        "Cache-Control": "no-store",
    }
    return StreamingResponse(
        _lines(columns, rows), media_type="text/csv; charset=utf-8", headers=headers
    )


def check_too_large(total: int) -> None:
    if total > MAX_ROWS:
        raise WorkflowError(
            413,
            "export_too_large",
            f"{total} rows match; narrow the filters to {MAX_ROWS} or fewer",
            max_rows=MAX_ROWS,
        )


def check_reveal(role: str | None, reveal: bool) -> None:
    if reveal and role != "supervisor":
        raise WorkflowError(
            403, "reveal_needs_supervisor", "only a supervisor can export identifiers"
        )
