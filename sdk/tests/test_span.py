"""Tests for the Span data model (sdk/argos/span.py)."""

import json
from datetime import datetime, timedelta, timezone

import pytest

from argos.span import Span, StepType, Status


def _minimal_span(**overrides):
    """Build a valid span with only the required fields, allowing overrides."""

    base = dict(
        service_name="research-assistant",
        agent_name="search",
        step_type=StepType.TOOL_CALL,
        name="web.search",
    )
    base.update(overrides)
    return Span(**base)


def test_required_fields_and_defaults():
    span = _minimal_span()
    assert span.service_name == "research-assistant"
    assert span.agent_name == "search"
    assert span.step_type is StepType.TOOL_CALL
    # Sensible defaults the emitter relies on:
    assert span.parent_span_id is None          # root span by default
    assert span.status is Status.OK
    assert span.tokens_in == 0 and span.tokens_out == 0
    assert span.cost_usd == 0.0
    assert span.attributes == {}
    assert span.redacted is False
    assert span.end_time is None


def test_step_type_accepts_plain_string():
    # Callers shouldn't have to import StepType; a string is coerced.
    span = _minimal_span(step_type="a2a_handoff")
    assert span.step_type is StepType.A2A_HANDOFF


def test_invalid_step_type_raises():
    # A typo must fail loudly, not silently store bad data.
    with pytest.raises(ValueError):
        _minimal_span(step_type="toolcall")


def test_invalid_status_raises():
    with pytest.raises(ValueError):
        _minimal_span(status="failure")


def test_duration_ms_none_until_ended():
    span = _minimal_span()
    assert span.duration_ms is None


def test_duration_ms_computed_from_times():
    start = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    end = start + timedelta(milliseconds=250)
    span = _minimal_span(start_time=start, end_time=end)
    assert span.duration_ms == pytest.approx(250.0)


def test_to_dict_is_json_friendly():
    start = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    end = start + timedelta(milliseconds=100)
    span = _minimal_span(
        step_type=StepType.LLM_CALL,
        status=Status.ERROR,
        error_message="boom",
        start_time=start,
        end_time=end,
        cost_usd=0.01,
    )
    data = span.to_dict()

    # Enums serialized as their string values, not "StepType.LLM_CALL".
    assert data["step_type"] == "llm_call"
    assert data["status"] == "error"
    # Datetimes as ISO strings; derived duration included.
    assert data["start_time"] == start.isoformat()
    assert data["duration_ms"] == pytest.approx(100.0)

    # And the whole thing must actually serialize.
    text = span.to_json()
    reloaded = json.loads(text)
    assert reloaded["error_message"] == "boom"
    assert reloaded["cost_usd"] == 0.01
