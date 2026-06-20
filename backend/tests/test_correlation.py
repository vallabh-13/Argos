"""Tests for the correlation engine (backend/correlation/engine.py).

Pure tests — hand-written span dicts, no ClickHouse. They cover the four things
that actually matter for stitching a multi-agent trace: out-of-order spans, a
real multi-agent tree, an orphaned span, and cost/metric rollup correctness.
"""

import json
from datetime import datetime, timedelta, timezone

from backend.correlation import build_trace

BASE = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


def _span(span_id, parent=None, *, agent="orchestrator", step_type="decision",
          name="step", start_offset_ms=0, dur_ms=100.0, status="ok",
          cost=0.0, tin=0, tout=0, trace_id="t1"):
    """Build one span dict shaped like the SDK's Span.to_dict()."""

    start = BASE + timedelta(milliseconds=start_offset_ms)
    end = start + timedelta(milliseconds=dur_ms) if dur_ms is not None else None
    return {
        "trace_id": trace_id,
        "span_id": span_id,
        "parent_span_id": parent,
        "service_name": "research-assistant",
        "agent_name": agent,
        "step_type": step_type,
        "name": name,
        "start_time": start.isoformat(),
        "end_time": end.isoformat() if end else None,
        "duration_ms": dur_ms,
        "status": status,
        "error_message": "boom" if status == "error" else None,
        "model": "claude-3-haiku",
        "tokens_in": tin,
        "tokens_out": tout,
        "cost_usd": cost,
        "attributes": {},
        "redacted": True,
    }


def _find(nodes, span_id):
    """Depth-first search for a node by span_id (cycle-safe via a visited set)."""

    stack, seen = list(nodes), set()
    while stack:
        node = stack.pop()
        if node.span_id in seen:
            continue
        seen.add(node.span_id)
        if node.span_id == span_id:
            return node
        stack.extend(node.children)
    return None


# --------------------------------------------------------------------------
# 1. Out-of-order arrival
# --------------------------------------------------------------------------
def test_out_of_order_spans_still_link():
    # Children listed BEFORE their parent — order must not matter.
    spans = [
        _span("c", parent="b", start_offset_ms=200),
        _span("b", parent="a", start_offset_ms=100),
        _span("a", parent=None, start_offset_ms=0),
    ]
    trace = build_trace(spans)

    assert [r.span_id for r in trace.roots] == ["a"]
    a = trace.roots[0]
    assert [c.span_id for c in a.children] == ["b"]
    assert [c.span_id for c in a.children[0].children] == ["c"]
    # Depth is stamped by structure, not arrival order.
    assert (a.depth, a.children[0].depth, a.children[0].children[0].depth) == (0, 1, 2)


# --------------------------------------------------------------------------
# 2. Multi-agent tree (the real shape: handoffs + tool call)
# --------------------------------------------------------------------------
def test_multi_agent_tree_structure():
    spans = [
        _span("root", None, agent="orchestrator", step_type="decision", start_offset_ms=0),
        _span("handoff1", "root", agent="orchestrator", step_type="a2a_handoff",
              name="-> search", start_offset_ms=10),
        _span("search", "handoff1", agent="search", step_type="llm_call", start_offset_ms=20),
        _span("tool", "search", agent="search", step_type="tool_call",
              name="web.search", start_offset_ms=30),
        _span("handoff2", "root", agent="orchestrator", step_type="a2a_handoff",
              name="-> summarizer", start_offset_ms=40),
        _span("summarize", "handoff2", agent="summarizer", step_type="llm_call",
              start_offset_ms=50),
    ]
    trace = build_trace(spans, "t1")

    assert trace.span_count == 6
    assert [r.span_id for r in trace.roots] == ["root"]

    root = trace.roots[0]
    # Two handoffs off the orchestrator, ordered by start_time.
    assert [c.span_id for c in root.children] == ["handoff1", "handoff2"]

    # search agent's subtree, reached through the first handoff.
    search = _find(trace.roots, "search")
    assert search.agent_name == "search"
    assert [c.span_id for c in search.children] == ["tool"]
    assert _find(trace.roots, "tool").depth == 3

    assert trace.summary.agent_count == 3  # orchestrator, search, summarizer
    assert trace.orphaned_span_ids == []


# --------------------------------------------------------------------------
# 3. Orphaned span (parent missing)
# --------------------------------------------------------------------------
def test_orphaned_span_is_surfaced_not_dropped():
    spans = [
        _span("root", None, start_offset_ms=0),
        # parent "ghost" was never ingested (dropped / late / other trace).
        _span("lost", "ghost", agent="search", start_offset_ms=10),
        _span("child_of_lost", "lost", agent="search", start_offset_ms=20),
    ]
    trace = build_trace(spans)

    assert "lost" in trace.orphaned_span_ids
    lost = _find(trace.roots, "lost")
    assert lost is not None and lost.orphaned is True
    # The subtree under an orphan is preserved, not lost.
    assert [c.span_id for c in lost.children] == ["child_of_lost"]
    # Genuine root is not flagged orphaned.
    assert _find(trace.roots, "root").orphaned is False
    # Every span still counted despite the missing parent.
    assert trace.span_count == 3


# --------------------------------------------------------------------------
# 4. Cost / metric rollup correctness
# --------------------------------------------------------------------------
def test_cost_and_metric_rollup():
    spans = [
        _span("a", None, start_offset_ms=0, dur_ms=100, cost=0.01, tin=100, tout=50),
        _span("b", "a", start_offset_ms=100, dur_ms=200, cost=0.02, tin=200, tout=80,
              status="error"),
        _span("c", "a", start_offset_ms=300, dur_ms=50, cost=0.005, tin=10, tout=5),
    ]
    s = build_trace(spans).summary

    assert abs(s.total_cost_usd - 0.035) < 1e-9
    assert s.total_tokens_in == 310
    assert s.total_tokens_out == 135
    assert s.total_tokens == 445
    assert s.step_count == 3
    assert s.error_count == 1
    # Sequential here, so sum of steps == wall-clock: 0..350ms.
    assert s.sum_step_ms == 350.0
    assert s.wall_clock_ms == 350.0


def test_parallel_steps_diverge_wall_clock_from_compute():
    # Two agents run at the same time: each 100ms, overlapping fully.
    spans = [
        _span("a", None, start_offset_ms=0, dur_ms=100),
        _span("b", None, start_offset_ms=0, dur_ms=100),
    ]
    s = build_trace(spans).summary
    # Compute time double-counts the overlap; wall-clock does not.
    assert s.sum_step_ms == 200.0
    assert s.wall_clock_ms == 100.0


# --------------------------------------------------------------------------
# Defensive extras
# --------------------------------------------------------------------------
def test_cycle_does_not_hang_and_counts_all_spans():
    # A -> B -> A, no root at all. Must not infinite-loop; both spans surface.
    spans = [
        _span("a", "b", start_offset_ms=0),
        _span("b", "a", start_offset_ms=10),
    ]
    trace = build_trace(spans)
    assert trace.span_count == 2
    assert trace.summary.step_count == 2
    # At least one of them is promoted to a root so the cycle is visible.
    assert len(trace.roots) >= 1


def test_to_dict_is_json_serializable():
    spans = [_span("a", None), _span("b", "a", start_offset_ms=10)]
    out = build_trace(spans).to_dict()
    # Round-trips through JSON with no custom encoder needed.
    assert json.loads(json.dumps(out))["span_count"] == 2
    assert out["roots"][0]["children"][0]["span_id"] == "b"
