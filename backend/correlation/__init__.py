"""Argos correlation engine — stitch flat spans into a causal multi-agent trace.

Public surface:

    from backend.correlation import build_trace
    trace = build_trace(spans)            # spans: list of span dicts
    trace.summary.total_cost_usd          # rolled-up per-run cost
    trace.to_dict()                       # JSON-friendly, for the dashboard

Reading spans from ClickHouse lives in ``store.fetch_spans`` (kept out of here so
importing the engine never requires a database driver).
"""

from .engine import build_trace
from .models import AssembledTrace, RunSummary, TraceNode

__all__ = ["build_trace", "AssembledTrace", "RunSummary", "TraceNode"]
