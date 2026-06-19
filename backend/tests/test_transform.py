"""Tests for span_dict_to_row (backend/storage/clickhouse.py).

This is the pure mapping from a span dict (as it arrives over Kafka as JSON) to
a ClickHouse row. No database required.
"""

import json
from datetime import datetime, timezone

from backend.storage.clickhouse import SPANS_COLUMNS, span_dict_to_row


def _span_dict(**overrides) -> dict:
    base = {
        "trace_id": "t1",
        "span_id": "s1",
        "parent_span_id": None,
        "service_name": "research-assistant",
        "agent_name": "search",
        "step_type": "tool_call",
        "name": "web.search",
        "start_time": "2026-01-01T12:00:00+00:00",
        "end_time": "2026-01-01T12:00:00.250000+00:00",
        "duration_ms": 250.0,
        "status": "ok",
        "error_message": None,
        "model": "claude-3-haiku",
        "tokens_in": 128,
        "tokens_out": 64,
        "cost_usd": 0.0011,
        "attributes": {"query": "fusion", "nested": {"k": "v"}},
        "redacted": True,
    }
    base.update(overrides)
    return base


def _row_field(row, column):
    return row[SPANS_COLUMNS.index(column)]


def test_row_has_one_value_per_column():
    row = span_dict_to_row(_span_dict())
    assert len(row) == len(SPANS_COLUMNS)


def test_timestamps_parsed_to_datetime():
    row = span_dict_to_row(_span_dict())
    start = _row_field(row, "start_time")
    end = _row_field(row, "end_time")
    assert isinstance(start, datetime)
    assert start == datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    assert isinstance(end, datetime)


def test_null_end_time_stays_none():
    row = span_dict_to_row(_span_dict(end_time=None))
    assert _row_field(row, "end_time") is None


def test_attributes_serialized_to_json_string():
    row = span_dict_to_row(_span_dict())
    attrs = _row_field(row, "attributes")
    assert isinstance(attrs, str)
    # Round-trips back to the original structure, nesting preserved.
    assert json.loads(attrs) == {"query": "fusion", "nested": {"k": "v"}}


def test_redacted_bool_becomes_uint8():
    assert _row_field(span_dict_to_row(_span_dict(redacted=True)), "redacted") == 1
    assert _row_field(span_dict_to_row(_span_dict(redacted=False)), "redacted") == 0


def test_numeric_types_coerced():
    row = span_dict_to_row(_span_dict(tokens_in="10", cost_usd="0.5"))
    assert _row_field(row, "tokens_in") == 10
    assert isinstance(_row_field(row, "tokens_in"), int)
    assert _row_field(row, "cost_usd") == 0.5
    assert isinstance(_row_field(row, "cost_usd"), float)


def test_missing_optional_fields_default_safely():
    minimal = {
        "trace_id": "t",
        "span_id": "s",
        "service_name": "svc",
        "agent_name": "a",
        "step_type": "decision",
        "name": "n",
        "start_time": "2026-01-01T00:00:00+00:00",
        "status": "ok",
    }
    row = span_dict_to_row(minimal)
    assert _row_field(row, "parent_span_id") is None
    assert _row_field(row, "tokens_in") == 0
    assert _row_field(row, "cost_usd") == 0.0
    assert _row_field(row, "attributes") == "{}"
    assert _row_field(row, "redacted") == 0
