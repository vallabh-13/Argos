"""Reading spans for a trace out of ClickHouse.

This is the only I/O in the correlation package — kept separate so the engine
stays pure and DB-free. It fetches every span sharing a ``trace_id`` and returns
them as plain dicts shaped exactly like the SDK's ``Span.to_dict()``, ready to
hand to :func:`backend.correlation.engine.build_trace`.
"""

from __future__ import annotations

import json
from typing import Any


def fetch_spans(client, trace_id: str) -> list[dict[str, Any]]:
    """Return every span for ``trace_id`` as a list of dicts.

    The query is **parameterized** (``{tid:String}``) rather than f-string
    interpolated, so a trace_id can never be misread as SQL — the right habit
    even when the input is internal. Spans come back ordered by start_time, which
    also matches the table's sort key so it's a cheap read.
    """

    result = client.query(
        "SELECT * FROM argos.spans WHERE trace_id = {tid:String} ORDER BY start_time",
        parameters={"tid": trace_id},
    )

    columns = result.column_names
    spans: list[dict[str, Any]] = []
    for row in result.result_rows:
        span = dict(zip(columns, row))
        # Undo the two storage encodings so the engine sees native types:
        #   attributes: JSON string -> dict   (see span_dict_to_row)
        #   redacted:   UInt8 0/1   -> bool
        attrs = span.get("attributes")
        if isinstance(attrs, str):
            span["attributes"] = json.loads(attrs) if attrs else {}
        span["redacted"] = bool(span.get("redacted"))
        spans.append(span)

    return spans
