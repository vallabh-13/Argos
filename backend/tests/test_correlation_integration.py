"""End-to-end correlation test (OFF by default).

Proves the *other* half of the pipeline from test_integration.py: spans inserted
into ClickHouse can be read back by ``store.fetch_spans`` and stitched into a
tree by the engine. Requires a running ClickHouse (``docker compose up -d``), so
it's skipped unless you opt in — CI, which has no database, stays green:

    # PowerShell
    $env:ARGOS_INTEGRATION="1"; pytest backend/tests/test_correlation_integration.py -v

The skip guard sits at module top, before any DB work, so collection never needs
a live database.
"""

import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from backend.correlation import build_trace
from backend.correlation.store import fetch_spans
from backend.storage.clickhouse import get_client, insert_spans, span_dict_to_row

pytestmark = pytest.mark.skipif(
    os.getenv("ARGOS_INTEGRATION") != "1",
    reason="set ARGOS_INTEGRATION=1 (and run docker compose up -d) to enable",
)

BASE = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


def _span(trace_id, span_id, parent, agent, step_type, name, off_ms, dur_ms,
          *, cost=0.0, tin=0, tout=0, status="ok"):
    start = BASE + timedelta(milliseconds=off_ms)
    end = start + timedelta(milliseconds=dur_ms)
    return {
        "trace_id": trace_id,
        "span_id": span_id,
        "parent_span_id": parent,
        "service_name": "research-assistant",
        "agent_name": agent,
        "step_type": step_type,
        "name": name,
        "start_time": start.isoformat(),
        "end_time": end.isoformat(),
        "duration_ms": dur_ms,
        "status": status,
        "error_message": "boom" if status == "error" else None,
        "model": "claude-3-haiku",
        "tokens_in": tin,
        "tokens_out": tout,
        "cost_usd": cost,
        "attributes": {"q": "fusion"},
        "redacted": True,
    }


def test_insert_fetch_and_assemble_roundtrip():
    client = get_client()
    trace_id = f"corr-itest-{uuid.uuid4()}"

    # A small multi-agent trace, inserted out of natural order to also exercise
    # the engine's order-independence through the real storage round-trip.
    spans = [
        _span(trace_id, "tool", "search", "search", "tool_call", "web.search",
              60, 400),
        _span(trace_id, "root", None, "orchestrator", "decision", "plan",
              0, 20, cost=0.001, tin=200, tout=40),
        _span(trace_id, "search", "root", "search", "llm_call", "decide query",
              20, 120, cost=0.004, tin=300, tout=90, status="error"),
    ]
    insert_spans(client, [span_dict_to_row(s) for s in spans])

    fetched = fetch_spans(client, trace_id)
    assert len(fetched) == 3
    # store.py decodes the storage encodings back to native types.
    assert isinstance(fetched[0]["attributes"], dict)
    assert isinstance(fetched[0]["redacted"], bool)

    trace = build_trace(fetched, trace_id)

    # Tree reconstructed correctly from DB rows: root -> search -> tool.
    assert [r.span_id for r in trace.roots] == ["root"]
    root = trace.roots[0]
    assert [c.span_id for c in root.children] == ["search"]
    assert [c.span_id for c in root.children[0].children] == ["tool"]
    assert trace.orphaned_span_ids == []

    # Rollup survives the round-trip.
    s = trace.summary
    assert abs(s.total_cost_usd - 0.005) < 1e-9
    assert s.total_tokens == 630
    assert s.step_count == 3
    assert s.error_count == 1
    assert s.agent_count == 2
