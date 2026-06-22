"""Regression test for the detector's ``--watch`` loop (backend/detection/__main__).

The bug this guards against: a new trace's spans stream into ClickHouse over
several poll cycles (Kafka -> consumer -> ClickHouse, root span emitted last). The
old watcher scored a trace the instant it first appeared — usually half-written —
found no failures, and (because Prometheus counters can't be un-incremented) never
looked at it again, so a real fail run never lit up the findings.

The fix scores a trace only once its span count stops growing. This test drives
the real ``_watch`` with a fake client whose ``fetch_spans`` returns a growing,
then stable, span list — and asserts ``record`` runs exactly once, on the COMPLETE
trace, with the failure finding present.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

import backend.detection.__main__ as cli
from backend.detection import metrics
from backend.correlation import persist

BASE = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


def _fail_span(i):
    """One failing tool_call span; 3+ of these trip repeated_tool_failure."""
    start = BASE + timedelta(milliseconds=i)
    return {
        "trace_id": "demo", "span_id": f"s{i}", "parent_span_id": None,
        "service_name": "svc", "agent_name": "search", "step_type": "tool_call",
        "name": "search-tools.web_search", "start_time": start.isoformat(),
        "end_time": start.isoformat(), "duration_ms": 1.0, "status": "error",
        "error_message": "malformed tool response", "model": None,
        "tokens_in": 0, "tokens_out": 0, "cost_usd": 0.0,
        "attributes": {}, "redacted": True,
    }


# Full trace = 8 failing spans. Each poll exposes more of them until it settles.
_FULL = [_fail_span(i) for i in range(8)]
_COUNT_BY_POLL = {1: 2, 2: 5, 3: 8}  # grows, then stays at 8 from poll 4 on


def test_watch_scores_trace_only_after_spans_settle(monkeypatch):
    state = {"poll": 0}

    def fake_recent_trace_ids(client, limit=50):
        if limit >= 500:
            return ["old"]          # startup seed: pre-existing traces are ignored
        state["poll"] += 1
        return ["old", "demo"]      # "old" must be skipped every poll

    def fake_fetch_spans(client, trace_id):
        if trace_id != "demo":
            return []
        n = _COUNT_BY_POLL.get(state["poll"], 8)
        return _FULL[:n]

    record = MagicMock()
    sleeps = {"n": 0}

    def fake_sleep(_):
        sleeps["n"] += 1
        if sleeps["n"] >= 5:        # let the loop run a few polls, then stop
            raise KeyboardInterrupt

    monkeypatch.setattr(cli, "recent_trace_ids", fake_recent_trace_ids)
    monkeypatch.setattr(cli, "fetch_spans", fake_fetch_spans)
    monkeypatch.setattr(cli.time, "sleep", fake_sleep)
    monkeypatch.setattr(metrics, "serve", lambda port: None)
    monkeypatch.setattr(metrics, "init_series", lambda: None)
    monkeypatch.setattr(metrics, "record", record)
    monkeypatch.setattr(persist, "ensure_trace_nodes_table", lambda client: None)
    monkeypatch.setattr(persist, "write_trace_nodes", lambda client, trace: 0)

    from backend.detection.models import DetectionConfig

    cli._watch(client=object(), config=DetectionConfig(), port=0, interval=0)

    # Scored exactly once — not on each growing poll, and not for "old".
    assert record.call_count == 1

    trace, findings = record.call_args.args
    # ...and only once the trace was COMPLETE (all 8 spans present).
    assert trace.span_count == 8
    rules = {f.rule for f in findings}
    assert "repeated_tool_failure" in rules  # the failure the old watcher missed


def test_watch_ignores_preexisting_traces(monkeypatch):
    """A trace that already exists at startup is never scored."""

    def fake_recent_trace_ids(client, limit=50):
        return ["old"]              # same trace at seed-time and every poll

    record = MagicMock()

    def fake_sleep(_):
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "recent_trace_ids", fake_recent_trace_ids)
    monkeypatch.setattr(cli, "fetch_spans",
                        lambda client, tid: _FULL if tid == "old" else [])
    monkeypatch.setattr(cli.time, "sleep", fake_sleep)
    monkeypatch.setattr(metrics, "serve", lambda port: None)
    monkeypatch.setattr(metrics, "init_series", lambda: None)
    monkeypatch.setattr(metrics, "record", record)
    monkeypatch.setattr(persist, "ensure_trace_nodes_table", lambda client: None)
    monkeypatch.setattr(persist, "write_trace_nodes", lambda client, trace: 0)

    from backend.detection.models import DetectionConfig

    cli._watch(client=object(), config=DetectionConfig(), port=0, interval=0)

    record.assert_not_called()
