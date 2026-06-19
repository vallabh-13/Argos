"""Sinks — where a finished span goes after it's built and redacted.

A *sink* is just ``Callable[[Span], None]``. ``trace_step`` hands every emitted
span to the configured sink; swapping the sink changes the destination without
touching any other code. That's the whole point of the design from Phase 1.

This module ships two:

* :func:`console_sink` — prints the span as JSON (the default; great for local dev).
* :class:`KafkaSink`    — publishes the span to a Kafka topic (Phase 2 pipeline).

The Kafka client is imported *lazily* inside ``KafkaSink``, so users who only
want console output never need ``confluent-kafka`` installed.
"""

from __future__ import annotations

import atexit
import json
from typing import Optional

from .span import Span


def console_sink(span: Span) -> None:
    """Print a span as pretty JSON to stdout. The default sink."""

    print(span.to_json())


class KafkaSink:
    """Publish spans to a Kafka topic (default ``argos.spans``).

    Usage (the whole adoption change is one argument to init_tracing):

        from argos import init_tracing, KafkaSink
        init_tracing(service="my-app", sink=KafkaSink(bootstrap_servers="localhost:29092"))

    Each span is serialized to JSON and keyed by its ``trace_id`` so that every
    span of one trace lands in the same partition — preserving per-trace order,
    which the correlation engine (Phase 3) relies on.
    """

    def __init__(
        self,
        bootstrap_servers: str = "localhost:29092",
        topic: str = "argos.spans",
        *,
        flush_on_exit: bool = True,
    ) -> None:
        # Lazy import: only people who actually use Kafka pay for the dependency.
        try:
            from confluent_kafka import Producer
        except ImportError as exc:  # pragma: no cover - import-guard message
            raise ImportError(
                "KafkaSink requires the 'kafka' extra. Install it with:\n"
                '    pip install "argos-sdk[kafka]"'
            ) from exc

        self.topic = topic
        # 'bootstrap.servers' is the entry-point address(es); the client then
        # discovers the rest of the cluster on its own.
        self._producer = Producer({"bootstrap.servers": bootstrap_servers})

        # Spans are produced asynchronously and buffered. Flush at interpreter
        # exit so a short-lived script (like the demo) doesn't drop its last
        # spans on the way out.
        if flush_on_exit:
            atexit.register(self.flush)

    def __call__(self, span: Span) -> None:
        payload = json.dumps(span.to_dict(), default=str).encode("utf-8")
        key = span.trace_id.encode("utf-8") if span.trace_id else None
        # poll(0) services delivery callbacks and keeps the internal queue from
        # filling up under load; it does not block.
        self._producer.poll(0)
        self._producer.produce(self.topic, value=payload, key=key)

    def flush(self, timeout: float = 10.0) -> None:
        """Block until all buffered spans are delivered (or the timeout hits)."""

        self._producer.flush(timeout)
