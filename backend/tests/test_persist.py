"""Tests for flatten_assembled (backend/correlation/persist.py).

Pure, no ClickHouse: build a small trace with the real engine, flatten it, and
check the rows are in pre-order with correct depth — the two things the Grafana
detail panel relies on to draw the indented tree.
"""

from __future__ import annotations

import json

from backend.correlation.engine import build_trace
from backend.correlation.persist import TRACE_NODE_COLUMNS, flatten_assembled


def _span(span_id, parent, agent, step_type, name, **kw):
    return {
        "trace_id": "t1",
        "span_id": span_id,
        "parent_span_id": parent,
        "agent_name": agent,
        "step_type": step_type,
        "name": name,
        "start_time": kw.get("start_time"),
        "duration_ms": kw.get("duration_ms", 0.0),
        "cost_usd": kw.get("cost_usd", 0.0),
        "status": kw.get("status", "ok"),
        "error_message": kw.get("error_message"),
        "attributes": kw.get("attributes", {}),
    }


def _as_dicts(rows):
    return [dict(zip(TRACE_NODE_COLUMNS, r)) for r in rows]


def test_flatten_is_preorder_with_depth():
    # root -> child -> grandchild, plus a second child of root.
    spans = [
        _span("root", None, "orchestrator", "decision", "orchestrate", start_time="2026-01-01T00:00:00+00:00"),
        _span("c1", "root", "search", "a2a_handoff", "a2a: o -> s", start_time="2026-01-01T00:00:01+00:00"),
        _span("g1", "c1", "search", "tool_call", "search-tools.web_search", start_time="2026-01-01T00:00:02+00:00"),
        _span("c2", "root", "search", "a2a_handoff", "a2a: s -> z", start_time="2026-01-01T00:00:03+00:00"),
    ]
    trace = build_trace(spans, "t1")
    rows = _as_dicts(flatten_assembled(trace))

    # One row per span, order_index dense and pre-order.
    assert [r["span_id"] for r in rows] == ["root", "c1", "g1", "c2"]
    assert [r["order_index"] for r in rows] == [0, 1, 2, 3]
    assert [r["depth"] for r in rows] == [0, 1, 2, 1]
    # parent links preserved.
    assert {r["span_id"]: r["parent_span_id"] for r in rows}["g1"] == "c1"


def test_attributes_serialized_to_json_string():
    spans = [
        _span("root", None, "search", "tool_call", "web", attributes={"mcp.tool": "web_search", "n": 3}),
    ]
    trace = build_trace(spans, "t1")
    row = _as_dicts(flatten_assembled(trace))[0]

    # attributes must land as a JSON *string* (the ClickHouse column type).
    assert isinstance(row["attributes"], str)
    assert json.loads(row["attributes"]) == {"mcp.tool": "web_search", "n": 3}


def test_error_span_round_trips_status_and_message():
    spans = [
        _span("root", None, "search", "tool_call", "web", status="error",
              error_message="malformed tool response"),
    ]
    trace = build_trace(spans, "t1")
    row = _as_dicts(flatten_assembled(trace))[0]

    assert row["status"] == "error"
    assert row["error_message"] == "malformed tool response"
    assert row["orphaned"] == 0
