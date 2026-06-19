"""Tests for the emitter (sdk/argos/tracing.py).

The unit tests in test_redaction.py prove the scrubber works in isolation.
These prove it's actually *wired into the emit path* — i.e. a secret attached to
a live step never reaches the sink. That end-to-end guarantee is the whole
security promise, so it deserves its own test.
"""

import pytest

from argos import init_tracing, trace_step
from argos.redaction import REDACTION_PLACEHOLDER
from argos.span import Span, Status, StepType


@pytest.fixture
def captured():
    """Install a sink that captures emitted spans instead of printing them."""

    spans: list[Span] = []
    init_tracing(service="test-service", sink=spans.append)
    return spans


def test_emits_one_span_with_core_fields(captured):
    with trace_step(agent_name="search", step_type="tool_call", name="web.search") as step:
        step.set_usage(model="claude-3-haiku", tokens_in=10, tokens_out=5)
        step.set_cost(0.002)

    assert len(captured) == 1
    span = captured[0]
    assert span.service_name == "test-service"
    assert span.agent_name == "search"
    assert span.step_type is StepType.TOOL_CALL
    assert span.model == "claude-3-haiku"
    assert span.tokens_in == 10 and span.tokens_out == 5
    assert span.cost_usd == 0.002
    # OTel gave us real ids and the span is closed out.
    assert span.trace_id and span.span_id
    assert span.end_time is not None
    assert span.status is Status.OK
    assert span.redacted is True
    # Regression: start must precede end, so duration is never negative.
    assert span.duration_ms is not None and span.duration_ms >= 0


def test_secrets_are_redacted_before_emit(captured):
    with trace_step(agent_name="search", step_type="tool_call", name="web.search") as step:
        step.set_attribute("api_key", "sk-abcdef0123456789ABCDEF")
        step.set_attribute("auth", {"password": "hunter2", "user": "demo"})
        step.set_attribute("query", "fusion energy")

    span = captured[0]
    assert span.attributes["api_key"] == REDACTION_PLACEHOLDER
    assert span.attributes["auth"]["password"] == REDACTION_PLACEHOLDER
    assert span.attributes["auth"]["user"] == "demo"      # safe value survives
    assert span.attributes["query"] == "fusion energy"    # safe value survives


def test_exception_is_recorded_as_error_and_reraised(captured):
    with pytest.raises(ValueError):
        with trace_step(agent_name="search", step_type="decision", name="choose") as step:
            step.set_attribute("stage", "early")
            raise ValueError("boom")

    # The span is still emitted, marked as an error.
    assert len(captured) == 1
    span = captured[0]
    assert span.status is Status.ERROR
    assert span.error_message == "boom"


def test_nested_steps_link_as_parent_child(captured):
    with trace_step(agent_name="orchestrator", step_type="decision", name="plan"):
        with trace_step(agent_name="search", step_type="tool_call", name="web.search"):
            pass

    # Inner span emits first (it closes first). Both share one trace_id, and the
    # inner span's parent is the outer span — the raw material for correlation.
    inner, outer = captured[0], captured[1]
    assert inner.trace_id == outer.trace_id
    assert inner.parent_span_id == outer.span_id
    assert outer.parent_span_id is None


def test_trace_step_before_init_raises():
    # Reset global state by importing the module and clearing service name.
    import argos.tracing as tracing

    tracing._service_name = None
    with pytest.raises(RuntimeError):
        with trace_step(agent_name="x", step_type="decision", name="y"):
            pass
