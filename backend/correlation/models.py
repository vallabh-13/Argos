"""Output data structures for the correlation engine.

These are the *result* of stitching a flat bag of spans into a causal trace.
They're deliberately plain dataclasses with no I/O and no ClickHouse import — the
engine (``engine.py``) produces them, and Phase 5's dashboard (or a JSON API)
reads them. ``to_dict()`` on each mirrors the SDK's ``Span.to_dict()`` so the
whole assembled trace serializes straight to JSON.

Three pieces:

* :class:`TraceNode`   — one span plus its children (a node in the causal tree).
* :class:`RunSummary`  — the per-run rollup (cost, tokens, duration, counts).
* :class:`AssembledTrace` — the top-level object: roots + summary + metadata.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional


def _json_safe(value: Any) -> Any:
    """Make a single span-field value JSON-friendly.

    ClickHouse hands timestamps back as ``datetime`` objects; the SDK's
    ``to_dict()`` already used ISO strings. We normalize datetimes to ISO so the
    output is uniform regardless of where the spans came from.
    """

    if isinstance(value, datetime):
        return value.isoformat()
    return value


@dataclass
class TraceNode:
    """One span in the reconstructed tree.

    Holds the original span dict untouched (so the dashboard has every field)
    plus three things the engine computes: ``depth`` (for tree layout),
    ``orphaned`` (its parent span was missing), and ``children``.
    """

    span: dict[str, Any]
    depth: int = 0
    orphaned: bool = False
    children: list["TraceNode"] = field(default_factory=list)

    # --- convenience accessors (read straight off the span dict) ----------
    @property
    def span_id(self) -> Optional[str]:
        return self.span.get("span_id")

    @property
    def parent_span_id(self) -> Optional[str]:
        return self.span.get("parent_span_id")

    @property
    def agent_name(self) -> Optional[str]:
        return self.span.get("agent_name")

    @property
    def name(self) -> Optional[str]:
        return self.span.get("name")

    def to_dict(self) -> dict[str, Any]:
        """JSON-friendly form: the span's fields + tree metadata + children."""

        return {
            **{k: _json_safe(v) for k, v in self.span.items()},
            "depth": self.depth,
            "orphaned": self.orphaned,
            "children": [child.to_dict() for child in self.children],
        }


@dataclass
class RunSummary:
    """Rolled-up totals for one run (one trace_id).

    Note the **two** duration numbers — they answer different questions and
    diverge the moment agents run in parallel:

    * ``wall_clock_ms`` — real elapsed time of the whole run
      (max end_time − min start_time). The headline "how long did it take".
    * ``sum_step_ms``  — total compute time across every step. Can *exceed*
      wall-clock when steps overlap (parallel agents), which is exactly the
      signal multi-agent tracing exists to show.
    """

    trace_id: str
    total_cost_usd: float = 0.0
    total_tokens_in: int = 0
    total_tokens_out: int = 0
    total_tokens: int = 0
    wall_clock_ms: float = 0.0
    sum_step_ms: float = 0.0
    step_count: int = 0
    error_count: int = 0
    agent_count: int = 0
    start_time: Optional[str] = None  # run boundary (ISO), min start across spans
    end_time: Optional[str] = None    # run boundary (ISO), max end across spans

    def to_dict(self) -> dict[str, Any]:
        return {
            "trace_id": self.trace_id,
            "total_cost_usd": self.total_cost_usd,
            "total_tokens_in": self.total_tokens_in,
            "total_tokens_out": self.total_tokens_out,
            "total_tokens": self.total_tokens,
            "wall_clock_ms": self.wall_clock_ms,
            "sum_step_ms": self.sum_step_ms,
            "step_count": self.step_count,
            "error_count": self.error_count,
            "agent_count": self.agent_count,
            "start_time": self.start_time,
            "end_time": self.end_time,
        }


@dataclass
class AssembledTrace:
    """The finished product: a fully-stitched, queryable multi-agent trace."""

    trace_id: str
    roots: list[TraceNode] = field(default_factory=list)
    summary: RunSummary = None  # type: ignore[assignment]
    orphaned_span_ids: list[str] = field(default_factory=list)
    span_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "trace_id": self.trace_id,
            "span_count": self.span_count,
            "orphaned_span_ids": self.orphaned_span_ids,
            "summary": self.summary.to_dict() if self.summary else None,
            "roots": [root.to_dict() for root in self.roots],
        }
