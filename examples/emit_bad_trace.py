"""Emit a deliberately misbehaving trace into the live stack — a detection demo.

Unlike the clean demo (one well-behaved span, which correctly trips nothing),
this inserts a trace that breaks all three detection rules so you can watch a
real Finding fire:

  * runaway loop          — the search agent calls web.search 6 times
  * repeated tool failure — flaky.api errors 4 times
  * cost spike            — the run totals well over the $1.00 limit

It writes the spans straight into ClickHouse (the same storage the Phase 2
consumer uses), then prints the trace_id. Feed that id to the detector:

    python examples/emit_bad_trace.py
    python -m backend.detection <printed-trace-id> --serve

Requires the stack up: docker compose up -d
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from backend.storage.clickhouse import get_client, insert_spans, span_dict_to_row

BASE = datetime.now(timezone.utc)


def _span(trace_id, span_id, parent, agent, step_type, name, off_ms,
          *, status="ok", cost=0.0, tin=0, tout=0):
    start = BASE + timedelta(milliseconds=off_ms)
    end = start + timedelta(milliseconds=20)
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
        "duration_ms": 20.0,
        "status": status,
        "error_message": "upstream timeout" if status == "error" else None,
        "model": "claude-3-haiku",
        "tokens_in": tin,
        "tokens_out": tout,
        "cost_usd": cost,
        "attributes": {"demo": "bad-trace"},
        "redacted": True,
    }


def main() -> None:
    trace_id = f"bad-{uuid.uuid4().hex[:8]}"

    spans = [
        _span(trace_id, "root", None, "orchestrator", "decision", "plan", 0,
              cost=0.40, tin=300, tout=60),
    ]
    # Runaway loop: search agent calls the same tool 10x. That's 2x the loop
    # threshold (5), so it escalates to CRITICAL.
    for i in range(10):
        spans.append(_span(trace_id, f"loop{i}", "root", "search",
                           "tool_call", "web.search", 10 + i))
    # Repeated tool failure: a different tool fails 6x = 2x the threshold (3),
    # also CRITICAL. At $0.30 each these also drive the cost spike.
    for i in range(6):
        spans.append(_span(trace_id, f"fail{i}", "root", "search",
                           "tool_call", "flaky.api", 50 + i,
                           status="error", cost=0.30))
    # Total cost = 0.40 + 6*0.30 = $2.20 > 2x the $1.00 limit -> CRITICAL.

    client = get_client()
    insert_spans(client, [span_dict_to_row(s) for s in spans])

    print(f"Inserted {len(spans)} spans for a misbehaving trace.")
    print(f"trace_id: {trace_id}")
    print(f"\nNow run:\n    python -m backend.detection {trace_id} --serve")


if __name__ == "__main__":
    main()
