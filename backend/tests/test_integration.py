"""End-to-end storage integration test (OFF by default).

Requires a running ClickHouse (``docker compose up -d``). It's skipped unless you
opt in, so CI — which has no database — stays green:

    # PowerShell
    $env:ARGOS_INTEGRATION="1"; pytest backend/tests/test_integration.py -v

It proves the storage half of the pipeline: a span row inserts and queries back.
"""

import os
import uuid
from datetime import datetime, timezone

import pytest

from backend.storage.clickhouse import get_client, insert_spans, query, span_dict_to_row

pytestmark = pytest.mark.skipif(
    os.getenv("ARGOS_INTEGRATION") != "1",
    reason="set ARGOS_INTEGRATION=1 (and run docker compose up -d) to enable",
)


def test_insert_and_query_roundtrip():
    client = get_client()
    trace_id = f"itest-{uuid.uuid4()}"

    span = {
        "trace_id": trace_id,
        "span_id": "s1",
        "parent_span_id": None,
        "service_name": "itest",
        "agent_name": "search",
        "step_type": "tool_call",
        "name": "web.search",
        "start_time": datetime.now(timezone.utc).isoformat(),
        "end_time": datetime.now(timezone.utc).isoformat(),
        "duration_ms": 1.0,
        "status": "ok",
        "error_message": None,
        "model": "claude-3-haiku",
        "tokens_in": 10,
        "tokens_out": 5,
        "cost_usd": 0.002,
        "attributes": {"query": "fusion"},
        "redacted": True,
    }

    insert_spans(client, [span_dict_to_row(span)])

    rows = query(
        client,
        f"SELECT agent_name, cost_usd FROM argos.spans WHERE trace_id = '{trace_id}'",
    )
    assert rows == [("search", 0.002)]
