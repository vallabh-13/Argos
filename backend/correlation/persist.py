"""Materialize an assembled trace into ClickHouse's ``argos.trace_nodes`` table.

The correlation engine (``engine.py``) is pure and produces an in-memory tree.
This module is the small I/O layer that flattens that tree into rows and writes
them, so the Grafana "Trace detail" panel can read a ready-to-display, depth-aware
view without re-deriving the parent->child structure in SQL.

Two pieces, mirroring the rest of the package's "pure core + thin I/O" split:

* :func:`flatten_assembled` — **pure**: tree -> ordered rows. Pre-order DFS, so the
  ``order_index`` it stamps reproduces the visual top-to-bottom tree when you
  ``ORDER BY order_index``. Cycle-safe (a malformed parent loop can't hang it).
* :func:`write_trace_nodes` — inserts those rows. The only ClickHouse call here.
"""

from __future__ import annotations

import json
from typing import Any

from .models import AssembledTrace, TraceNode

# Insert column order — must match the trace_nodes table in schema.sql.
# `written_at` is omitted on purpose: ClickHouse fills it via DEFAULT now() and
# uses it as the ReplacingMergeTree version.
TRACE_NODE_COLUMNS: tuple[str, ...] = (
    "trace_id",
    "span_id",
    "parent_span_id",
    "order_index",
    "depth",
    "agent_name",
    "step_type",
    "name",
    "duration_ms",
    "cost_usd",
    "status",
    "error_message",
    "attributes",
    "orphaned",
)


# Mirrors the trace_nodes DDL in backend/storage/schema.sql. We keep a copy here
# so the table can be created on demand against an EXISTING ClickHouse volume —
# the schema.sql init script only runs on a fresh volume, so without this an older
# stack (Phases 2-5) would have no trace_nodes table until a data-wiping reset.
_CREATE_TRACE_NODES = """
CREATE TABLE IF NOT EXISTS argos.trace_nodes
(
    trace_id        String,
    span_id         String,
    parent_span_id  Nullable(String),
    order_index     UInt32,
    depth           UInt16,
    agent_name      LowCardinality(String),
    step_type       LowCardinality(String),
    name            String,
    duration_ms     Float64 DEFAULT 0,
    cost_usd        Float64 DEFAULT 0,
    status          LowCardinality(String),
    error_message   Nullable(String),
    attributes      String DEFAULT '{}',
    orphaned        UInt8 DEFAULT 0,
    written_at      DateTime DEFAULT now()
)
ENGINE = ReplacingMergeTree(written_at)
ORDER BY (trace_id, span_id)
"""


def ensure_trace_nodes_table(client) -> None:
    """Create argos.trace_nodes if it doesn't exist (idempotent)."""

    client.command(_CREATE_TRACE_NODES)


def flatten_assembled(trace: AssembledTrace) -> list[list[Any]]:
    """Flatten the assembled tree into ordered rows (pre-order DFS).

    Each row follows :data:`TRACE_NODE_COLUMNS`. ``order_index`` increases with
    pre-order position so a later ``ORDER BY order_index`` redraws the tree as it
    looks on screen; ``depth`` (already computed by the engine) drives indentation.
    """

    rows: list[list[Any]] = []
    visited: set[str] = set()
    order = 0

    def walk(node: TraceNode) -> None:
        nonlocal order
        sid = node.span_id
        if sid in visited:
            return  # cycle guard: never emit the same span twice
        if sid is not None:
            visited.add(sid)

        span = node.span
        attrs = span.get("attributes", {})
        rows.append(
            [
                trace.trace_id,
                sid or "",
                node.parent_span_id,
                order,
                node.depth,
                span.get("agent_name") or "",
                span.get("step_type") or "",
                span.get("name") or "",
                float(span.get("duration_ms") or 0.0),
                float(span.get("cost_usd") or 0.0),
                span.get("status") or "ok",
                span.get("error_message"),
                attrs if isinstance(attrs, str) else json.dumps(attrs, default=str),
                1 if node.orphaned else 0,
            ]
        )
        order += 1
        for child in node.children:
            walk(child)

    for root in trace.roots:
        walk(root)

    return rows


def write_trace_nodes(client, trace: AssembledTrace) -> int:
    """Persist an assembled trace's nodes to ``argos.trace_nodes``.

    Returns the number of rows written. Re-writing the same trace is safe — the
    ReplacingMergeTree dedups by (trace_id, span_id) on its newest ``written_at``.
    """

    rows = flatten_assembled(trace)
    if not rows:
        return 0
    client.insert("argos.trace_nodes", rows, column_names=list(TRACE_NODE_COLUMNS))
    return len(rows)
