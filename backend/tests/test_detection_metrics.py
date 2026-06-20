"""Light test for the Prometheus exporter (backend/detection/metrics.py).

Doesn't need a running Prometheus — it just confirms ``record()`` moves the right
counters/gauges in the in-process registry. (prometheus_client is a backend
dependency, so it's installed in CI.)
"""

from datetime import datetime, timedelta, timezone

from prometheus_client import REGISTRY

from backend.correlation import build_trace
from backend.detection import DetectionConfig, run_detection
from backend.detection import metrics

BASE = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


def _loop_span(i):
    start = BASE + timedelta(milliseconds=i)
    return {
        "trace_id": "t1", "span_id": f"s{i}", "parent_span_id": None,
        "service_name": "svc", "agent_name": "search", "step_type": "tool_call",
        "name": "web.search", "start_time": start.isoformat(),
        "end_time": start.isoformat(), "duration_ms": 1.0, "status": "ok",
        "error_message": None, "model": "m", "tokens_in": 0, "tokens_out": 0,
        "cost_usd": 2.0, "attributes": {}, "redacted": True,
    }


def test_record_moves_counters_and_gauges():
    trace = build_trace([_loop_span(i) for i in range(6)], "t1")
    findings = run_detection(trace, DetectionConfig())

    before = REGISTRY.get_sample_value("argos_traces_evaluated_total") or 0.0
    metrics.record(trace, findings)
    after = REGISTRY.get_sample_value("argos_traces_evaluated_total") or 0.0
    assert after == before + 1

    # The loop finding was counted under its rule/severity label. 6 repeats with
    # loop_count=5 is a WARNING (CRITICAL needs >= 2x the threshold).
    loop_count = REGISTRY.get_sample_value(
        "argos_findings_total", {"rule": "runaway_loop", "severity": "warning"}
    )
    assert loop_count is not None and loop_count >= 1

    # Gauges reflect the most recent run.
    assert REGISTRY.get_sample_value("argos_last_run_cost_usd") == 12.0  # 6 * $2.00
    assert REGISTRY.get_sample_value("argos_last_run_loops") == 6.0
