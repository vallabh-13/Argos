"""Tests for the detection rules (backend/detection/rules.py) + engine.

Pure tests: build an AssembledTrace from hand-written spans (via the real
correlation engine, so the trace shape is authentic), then assert each rule
fires only when it should — including the exact boundary at each threshold.
"""

from datetime import datetime, timedelta, timezone

from backend.correlation import build_trace
from backend.detection import DetectionConfig, run_detection
from backend.detection.rules import (
    detect_cost_spikes,
    detect_repeated_tool_failures,
    detect_runaway_loops,
)

BASE = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
CFG = DetectionConfig()  # defaults: loop_count=5, failure_count=3, cost_limit_usd=1.00


def _span(span_id, parent, *, agent="search", step_type="tool_call",
          name="web.search", off_ms=0, dur_ms=10.0, status="ok", cost=0.0):
    start = BASE + timedelta(milliseconds=off_ms)
    end = start + timedelta(milliseconds=dur_ms)
    return {
        "trace_id": "t1",
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
        "model": "m",
        "tokens_in": 0,
        "tokens_out": 0,
        "cost_usd": cost,
        "attributes": {},
        "redacted": True,
    }


def _trace(spans):
    return build_trace(spans, "t1")


def _clean_trace():
    """A normal small run: a few distinct steps, cheap, no errors."""
    return _trace([
        _span("root", None, agent="orchestrator", step_type="decision", name="plan"),
        _span("h", "root", agent="orchestrator", step_type="a2a_handoff", name="-> search"),
        _span("llm", "h", agent="search", step_type="llm_call", name="decide", cost=0.01),
        _span("tool", "llm", agent="search", step_type="tool_call", name="web.search"),
    ])


# --------------------------------------------------------------------------
# Clean trace trips nothing
# --------------------------------------------------------------------------
def test_clean_trace_trips_nothing():
    assert run_detection(_clean_trace(), CFG) == []


# --------------------------------------------------------------------------
# Runaway loop
# --------------------------------------------------------------------------
def _looping_trace(n):
    """Root + n identical tool calls by the same agent (same signature)."""
    spans = [_span("root", None, agent="orchestrator", step_type="decision", name="plan")]
    for i in range(n):
        spans.append(_span(f"loop{i}", "root", agent="search",
                           step_type="tool_call", name="web.search", off_ms=i + 1))
    return _trace(spans)


def test_looping_trace_trips_loop_rule():
    findings = detect_runaway_loops(_looping_trace(6), CFG)
    assert len(findings) == 1
    assert findings[0].rule == "runaway_loop"
    assert findings[0].evidence["count"] == 6
    assert len(findings[0].span_ids) == 6


def test_loop_boundary_exactly_at_threshold_fires():
    # count == loop_count (5) fires (>=).
    assert len(detect_runaway_loops(_looping_trace(5), CFG)) == 1


def test_loop_boundary_just_below_threshold_silent():
    # count == loop_count - 1 (4) does not fire.
    assert detect_runaway_loops(_looping_trace(4), CFG) == []


def test_loop_escalates_to_critical_at_double():
    # 10 == 2 * loop_count -> CRITICAL.
    findings = detect_runaway_loops(_looping_trace(10), CFG)
    assert findings[0].severity.value == "critical"


# --------------------------------------------------------------------------
# Repeated tool failure
# --------------------------------------------------------------------------
def _failing_trace(n_fail, *, step_type="tool_call"):
    """Root + n failing tool calls of the SAME tool."""
    spans = [_span("root", None, agent="orchestrator", step_type="decision", name="plan")]
    for i in range(n_fail):
        spans.append(_span(f"f{i}", "root", agent="search", step_type=step_type,
                           name="flaky.api", off_ms=i + 1, status="error"))
    return _trace(spans)


def test_failing_trace_trips_failure_rule():
    findings = detect_repeated_tool_failures(_failing_trace(3), CFG)
    assert len(findings) == 1
    assert findings[0].rule == "repeated_tool_failure"
    assert findings[0].evidence["count"] == 3
    assert findings[0].evidence["error_messages"] == ["boom", "boom", "boom"]


def test_failure_boundary_exactly_at_threshold_fires():
    assert len(detect_repeated_tool_failures(_failing_trace(3), CFG)) == 1


def test_failure_boundary_just_below_threshold_silent():
    assert detect_repeated_tool_failures(_failing_trace(2), CFG) == []


def test_failure_rule_ignores_non_tool_errors():
    # Same count of errors, but they're llm_call errors, not tool_call -> ignored.
    assert detect_repeated_tool_failures(_failing_trace(3, step_type="llm_call"), CFG) == []


# --------------------------------------------------------------------------
# Cost spike
# --------------------------------------------------------------------------
def _cost_trace(total_cost):
    return _trace([
        _span("root", None, agent="orchestrator", step_type="decision",
              name="plan", cost=total_cost)
    ])


def test_expensive_trace_trips_cost_rule():
    findings = detect_cost_spikes(_cost_trace(1.50), CFG)
    assert len(findings) == 1
    assert findings[0].rule == "cost_spike"
    assert findings[0].evidence["total_cost_usd"] == 1.50


def test_cost_boundary_exactly_at_limit_is_silent():
    # cost == limit is OK; only strictly greater (>) trips.
    assert detect_cost_spikes(_cost_trace(1.00), CFG) == []


def test_cost_boundary_just_over_limit_fires():
    assert len(detect_cost_spikes(_cost_trace(1.0001), CFG)) == 1


def test_cost_escalates_to_critical_past_double():
    findings = detect_cost_spikes(_cost_trace(2.50), CFG)
    assert findings[0].severity.value == "critical"


# --------------------------------------------------------------------------
# Engine: rules are independent; thresholds are configurable
# --------------------------------------------------------------------------
def test_engine_runs_all_rules_and_isolates_them():
    # A loop of cheap successful calls trips ONLY the loop rule.
    findings = run_detection(_looping_trace(6), CFG)
    assert {f.rule for f in findings} == {"runaway_loop"}


def test_thresholds_are_configurable_not_hardcoded():
    # With a stricter loop_count, a 4-call loop that was silent now trips.
    strict = DetectionConfig(loop_count=4)
    assert detect_runaway_loops(_looping_trace(4), strict)
    assert detect_runaway_loops(_looping_trace(4), CFG) == []


def test_config_from_env(monkeypatch):
    monkeypatch.setenv("ARGOS_LOOP_COUNT", "2")
    monkeypatch.setenv("ARGOS_COST_LIMIT_USD", "0.50")
    cfg = DetectionConfig.from_env()
    assert cfg.loop_count == 2
    assert cfg.cost_limit_usd == 0.50
    assert cfg.failure_count == 3  # untouched -> default


def test_config_from_config_layers_file_then_env(tmp_path, monkeypatch):
    # Precedence: built-in defaults < argos.config.yml < ARGOS_* env vars.
    cfg_file = tmp_path / "argos.config.yml"
    cfg_file.write_text(
        "detection:\n  loop_count: 7\n  failure_count: 4\n  cost_limit_usd: 2.50\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("ARGOS_CONFIG", str(cfg_file))
    monkeypatch.setenv("ARGOS_LOOP_COUNT", "9")  # env overrides the file's 7

    cfg = DetectionConfig.from_config()
    assert cfg.loop_count == 9       # env wins
    assert cfg.failure_count == 4    # from file
    assert cfg.cost_limit_usd == 2.50  # from file
