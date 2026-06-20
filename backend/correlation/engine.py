"""The correlation engine — the core of Argos.

Turns a **flat list of spans** that share a ``trace_id`` into a **causal tree**
(parent → children), and rolls up cost / tokens / duration / error counts for the
whole run. This is the piece that reconstructs "what actually happened" across
several agents and MCP/A2A handoffs.

It is a **pure function**: it takes a list of span dicts and returns an
:class:`AssembledTrace`. No ClickHouse, no Kafka, no I/O — so it's trivially
testable with hand-written dicts and no infrastructure running. Reading spans out
of ClickHouse lives separately in ``store.py``.

The key trick (so order of arrival never matters): **index every span first,
then link**. A child seen before its parent still links correctly because by the
time we link, every span is already in the index.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from .models import AssembledTrace, RunSummary, TraceNode

# Used as a sort fallback so spans with no start_time sort last instead of
# blowing up on a None comparison.
_FAR_FUTURE = datetime.max.replace(tzinfo=timezone.utc)


def _as_dt(value: Any) -> Optional[datetime]:
    """Normalize a timestamp to a tz-aware datetime (or None).

    Tolerant on purpose: ClickHouse hands back ``datetime`` objects, while the
    SDK's ``to_dict()`` produced ISO strings. Accepting both means the same
    engine works on real DB rows *and* on plain test dicts. Naive datetimes are
    assumed UTC (the SDK only ever emits UTC).
    """

    if value is None:
        return None
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value
    return None


def _duration_ms(span: dict[str, Any]) -> float:
    """A span's own duration in ms.

    Prefer the stored ``duration_ms``; if it's missing but we have both
    timestamps, derive it; otherwise (an in-progress span) treat it as 0.
    """

    d = span.get("duration_ms")
    if d is not None:
        return float(d)
    start, end = _as_dt(span.get("start_time")), _as_dt(span.get("end_time"))
    if start and end:
        return (end - start).total_seconds() * 1000.0
    return 0.0


def _sort_key(node: TraceNode) -> datetime:
    """Order siblings/roots chronologically; undated spans go last."""

    return _as_dt(node.span.get("start_time")) or _FAR_FUTURE


def build_trace(
    spans: list[dict[str, Any]],
    trace_id: Optional[str] = None,
) -> AssembledTrace:
    """Stitch a list of span dicts into one :class:`AssembledTrace`.

    If ``trace_id`` is given, only spans with that id are used (defensive — the
    caller might hand us a mixed bag). If it's omitted, we adopt the trace_id of
    the first span and assume the list is already one trace.
    """

    if trace_id is not None:
        spans = [s for s in spans if s.get("trace_id") == trace_id]
    elif spans:
        trace_id = spans[0].get("trace_id", "")
    else:
        trace_id = ""

    # --- Pass 1: index every span by span_id -----------------------------
    # Build all nodes up front. This is what makes out-of-order input a
    # non-issue: linking in pass 2 can always find any parent that exists.
    by_id: dict[str, TraceNode] = {}
    for span in spans:
        sid = span.get("span_id")
        if sid is None:
            continue  # a span with no id can't be placed in the tree; skip it
        # Last-writer-wins on duplicate span_ids (e.g. a re-emitted span).
        by_id[sid] = TraceNode(span=span)

    # --- Pass 2: link each span to its parent ----------------------------
    roots: list[TraceNode] = []
    orphaned_span_ids: list[str] = []
    for node in by_id.values():
        parent_id = node.parent_span_id
        if parent_id is None:
            roots.append(node)                 # a genuine root (no parent)
        elif parent_id in by_id:
            by_id[parent_id].children.append(node)  # normal parent → child link
        else:
            # Parent referenced but absent — dropped span, late arrival, or a
            # handoff from a trace we didn't fetch. Keep the subtree and flag it
            # rather than silently losing data.
            node.orphaned = True
            orphaned_span_ids.append(node.span_id)  # type: ignore[arg-type]
            roots.append(node)

    # --- Surface cycle / disconnected spans (defensive) ------------------
    # A pure cycle (A→B→A) has no root and no orphan, so it would vanish from
    # the tree. Promote any span not reachable from the current roots to a root
    # so it still appears. The visited guard below guarantees we never loop
    # forever on such a cycle. (Detecting a loop as a *problem* is Phase 4; here
    # we only refuse to hang on one.)
    reachable: set[str] = set()
    _mark_reachable(roots, reachable)
    for sid, node in by_id.items():
        if sid not in reachable:
            roots.append(node)
            _mark_reachable([node], reachable)

    # --- Order + assign depth for layout ---------------------------------
    roots.sort(key=_sort_key)
    _order_and_depth(roots, depth=0, visited=set())

    summary = _summarize(spans, trace_id, orphaned_span_ids)
    return AssembledTrace(
        trace_id=trace_id,
        roots=roots,
        summary=summary,
        orphaned_span_ids=orphaned_span_ids,
        span_count=len(by_id),
    )


def _mark_reachable(nodes: list[TraceNode], reachable: set[str]) -> None:
    """DFS that records every span_id reachable from ``nodes`` (cycle-safe)."""

    for node in nodes:
        sid = node.span_id
        if sid in reachable:
            continue
        if sid is not None:
            reachable.add(sid)
        _mark_reachable(node.children, reachable)


def _order_and_depth(nodes: list[TraceNode], depth: int, visited: set[str]) -> None:
    """Sort children chronologically and stamp each node's depth.

    ``visited`` makes the recursion cycle-proof: if a malformed parent loop ever
    points back at an ancestor, we stop instead of recursing forever.
    """

    for node in nodes:
        sid = node.span_id
        if sid in visited:
            continue
        if sid is not None:
            visited.add(sid)
        node.depth = depth
        node.children.sort(key=_sort_key)
        _order_and_depth(node.children, depth + 1, visited)


def _summarize(
    spans: list[dict[str, Any]],
    trace_id: str,
    orphaned_span_ids: list[str],
) -> RunSummary:
    """Roll up per-run totals with a **flat sum** over every span.

    Summing flat (not by walking the tree) is what keeps the totals correct even
    when there are orphans or cycles — every span is counted exactly once,
    regardless of where it landed in the tree.
    """

    summary = RunSummary(trace_id=trace_id, step_count=len(spans))

    starts: list[datetime] = []
    ends: list[datetime] = []
    agents: set[str] = set()

    for span in spans:
        summary.total_cost_usd += float(span.get("cost_usd", 0.0) or 0.0)
        summary.total_tokens_in += int(span.get("tokens_in", 0) or 0)
        summary.total_tokens_out += int(span.get("tokens_out", 0) or 0)
        summary.sum_step_ms += _duration_ms(span)
        if span.get("status") == "error":
            summary.error_count += 1
        if span.get("agent_name"):
            agents.add(span["agent_name"])

        start = _as_dt(span.get("start_time"))
        end = _as_dt(span.get("end_time"))
        if start:
            starts.append(start)
        if end:
            ends.append(end)

    summary.total_tokens = summary.total_tokens_in + summary.total_tokens_out
    summary.agent_count = len(agents)

    # Wall-clock = span of real time the run occupied. Fall back to start
    # timestamps when no span has finished yet (everything still in progress).
    if starts:
        run_start = min(starts)
        run_end = max(ends) if ends else max(starts)
        summary.wall_clock_ms = max(0.0, (run_end - run_start).total_seconds() * 1000.0)
        summary.start_time = run_start.isoformat()
        summary.end_time = run_end.isoformat()

    return summary
