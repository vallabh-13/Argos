"""Proof that the demo's *real* failure behavior trips the Phase 4 detectors.

This is the load-bearing test for the "no fabricated traces" requirement. It does
NOT hand-build spans like emit_bad_trace.py. Instead it runs the actual search
agent against the actual garbage-returning tool, captures the spans the SDK
genuinely emits, stitches them with the real correlation engine, and asserts the
real detection rules fire. No AWS: the LLM is never reached in the failure path
(the tool fails before extraction), so a dummy stands in.

If someone later "fixes" the agent so a bad tool no longer loops, this test goes
red — which is exactly the regression guard we want.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from argos import init_tracing

from backend.correlation.engine import build_trace
from backend.detection.engine import run_detection
from backend.detection.models import DetectionConfig

# The demo agents live in examples/research-assistant and import their siblings
# by name (pricing, tools.web_search). Put that dir on sys.path so we can import
# the real agent under test.
_DEMO_DIR = Path(__file__).resolve().parents[2] / "examples" / "research-assistant"
sys.path.insert(0, str(_DEMO_DIR))

from agents.search import gather  # noqa: E402  (import after sys.path tweak)


class _DummyLLM:
    """Never called in the failure path (the tool fails before extraction)."""

    model = "dummy"

    def converse(self, *, system, messages):  # pragma: no cover - defensive
        raise AssertionError("LLM should not be called when the tool keeps failing")


@pytest.fixture
def captured_spans(monkeypatch):
    """Capture every emitted span as a plain dict (engine input shape)."""

    # Pin thresholds independent of any ARGOS_* env the runner may have set.
    monkeypatch.delenv("ARGOS_DEMO_MAX_RETRIES", raising=False)
    spans: list[dict] = []
    init_tracing(service="research-assistant", sink=lambda s: spans.append(s.to_dict()))
    return spans


def test_failing_tool_produces_real_loop_that_trips_detection(captured_spans):
    # Run the genuine agent with the genuine garbage tool, 6 retries.
    findings_input = gather(_DummyLLM(), "fusion energy", scenario="fail", max_retries=6)
    assert findings_input["failed"] is True

    # Every emitted tool_call must be a real error span with a stable signature.
    tool_calls = [s for s in captured_spans if s["step_type"] == "tool_call"]
    assert len(tool_calls) == 6
    assert all(s["status"] == "error" for s in tool_calls)
    assert all(s["name"] == "search-tools.web_search" for s in tool_calls)

    # Stitch with the real engine and score with the real rules (default thresholds).
    trace = build_trace(captured_spans)
    findings = run_detection(trace, DetectionConfig())
    rules = {f.rule for f in findings}

    assert "runaway_loop" in rules, "6 identical tool_calls should trip runaway_loop (>=5)"
    assert "repeated_tool_failure" in rules, "6 tool errors should trip repeated_tool_failure (>=3)"

    loop = next(f for f in findings if f.rule == "runaway_loop")
    fail = next(f for f in findings if f.rule == "repeated_tool_failure")
    assert loop.evidence["count"] == 6
    assert fail.evidence["count"] == 6
    # Severity math with 6 retries:
    #   runaway_loop escalates at 2x loop_count (=10); 6 < 10 -> WARNING.
    #   repeated_tool_failure escalates at 2x failure_count (=6); 6 >= 6 -> CRITICAL.
    # (Raise ARGOS_DEMO_MAX_RETRIES to >=10 in the live demo to push the loop to
    # CRITICAL too.)
    assert loop.severity.value == "warning"
    assert fail.severity.value == "critical"
