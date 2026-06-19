"""Tests for span sinks (sdk/argos/sinks.py).

The Kafka test uses a fake producer so it needs no running broker — it verifies
the *serialization and routing* logic: right topic, right key, valid JSON.
"""

import json

from argos.sinks import KafkaSink, console_sink
from argos.span import Span, StepType


class FakeProducer:
    """Stand-in for confluent_kafka.Producer that records produce() calls."""

    def __init__(self, conf):
        self.conf = conf
        self.produced: list[tuple] = []
        self.flushed = False

    def poll(self, timeout):  # noqa: D401 - matches the real API
        return 0

    def produce(self, topic, value=None, key=None):
        self.produced.append((topic, key, value))

    def flush(self, timeout=None):
        self.flushed = True
        return 0


def _span() -> Span:
    return Span(
        service_name="research-assistant",
        agent_name="search",
        step_type=StepType.TOOL_CALL,
        name="web.search",
        trace_id="trace-123",
        span_id="span-abc",
        cost_usd=0.0011,
    )


def test_kafka_sink_produces_keyed_json(monkeypatch):
    import confluent_kafka

    monkeypatch.setattr(confluent_kafka, "Producer", FakeProducer)
    sink = KafkaSink(bootstrap_servers="x:9092", topic="argos.spans", flush_on_exit=False)

    sink(_span())

    assert len(sink._producer.produced) == 1
    topic, key, value = sink._producer.produced[0]
    assert topic == "argos.spans"
    # Keyed by trace_id so all spans of a trace share a partition (ordering).
    assert key == b"trace-123"
    payload = json.loads(value.decode("utf-8"))
    assert payload["agent_name"] == "search"
    assert payload["trace_id"] == "trace-123"
    assert payload["cost_usd"] == 0.0011


def test_kafka_sink_flush_delegates(monkeypatch):
    import confluent_kafka

    monkeypatch.setattr(confluent_kafka, "Producer", FakeProducer)
    sink = KafkaSink(flush_on_exit=False)
    sink.flush()
    assert sink._producer.flushed is True


def test_console_sink_prints_json(capsys):
    console_sink(_span())
    out = capsys.readouterr().out
    assert '"agent_name": "search"' in out
    assert '"trace_id": "trace-123"' in out
