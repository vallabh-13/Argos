"""The ingest consumer — reads spans off Kafka and writes them to ClickHouse.

This is the middle of the Phase 2 pipeline:

    app (SDK) --> Kafka "argos.spans" --> [THIS] --> ClickHouse argos.spans

Run it from the repo root (after `docker compose up -d` and installing
backend/requirements.txt):

    python -m backend.ingest.consumer

Two design choices worth understanding:

* **Micro-batching.** ClickHouse is built for big, infrequent inserts, not one
  row at a time. So we accumulate spans and flush when the batch is full
  (``ARGOS_BATCH_MAX``) or a little time has passed (``ARGOS_FLUSH_SECONDS``).

* **At-least-once delivery.** We turn off Kafka's auto-commit and commit offsets
  ONLY after a batch is safely in ClickHouse. If the process dies mid-batch, on
  restart it re-reads from the last committed offset — so we never lose a span.
  The tradeoff: a crash between insert and commit can re-insert a batch, i.e.
  duplicates are possible (plain MergeTree doesn't dedupe).
"""

from __future__ import annotations

import json
import os
import sys
import time
from typing import Optional

from backend.storage.clickhouse import get_client, insert_spans, span_dict_to_row

BOOTSTRAP = os.getenv("ARGOS_KAFKA_BOOTSTRAP", "localhost:29092")
TOPIC = os.getenv("ARGOS_KAFKA_TOPIC", "argos.spans")
GROUP = os.getenv("ARGOS_KAFKA_GROUP", "argos-ingest")
BATCH_MAX = int(os.getenv("ARGOS_BATCH_MAX", "100"))
FLUSH_SECONDS = float(os.getenv("ARGOS_FLUSH_SECONDS", "2.0"))


def build_consumer():
    """Create a Kafka consumer configured for at-least-once ingestion."""

    from confluent_kafka import Consumer

    return Consumer(
        {
            "bootstrap.servers": BOOTSTRAP,
            "group.id": GROUP,
            # On first run with no committed offset, start at the beginning so we
            # don't miss spans produced before the consumer was up.
            "auto.offset.reset": "earliest",
            # We commit manually, after the ClickHouse insert succeeds.
            "enable.auto.commit": False,
        }
    )


def _flush(client, consumer, batch) -> None:
    """Insert the batch into ClickHouse, then commit the Kafka offsets."""

    insert_spans(client, batch)
    consumer.commit(asynchronous=False)  # synchronous: be sure it stuck
    print(f"[argos-ingest] inserted {len(batch)} span(s)")


def connect_clickhouse(timeout: float = 60.0, interval: float = 2.0):
    """Get a ClickHouse client, retrying until the server actually serves queries.

    Right after `docker compose up`, ClickHouse's TCP port opens seconds before
    it can answer HTTP queries, and its `argos` database is created by an init
    script that may not have run yet. A plain ``get_client()`` then crashes. So we
    retry connect+``SELECT 1`` with backoff — which is exactly what lets the menu
    start this consumer the moment the backend comes up, with no race.
    """

    deadline = time.monotonic() + timeout
    last_error: Optional[Exception] = None
    while time.monotonic() < deadline:
        try:
            client = get_client()
            client.query("SELECT 1")  # prove it's serving, not just listening
            return client
        except Exception as exc:  # noqa: BLE001 - connection refused / db missing / etc.
            last_error = exc
            print(f"[argos-ingest] waiting for ClickHouse to be ready... ({exc})",
                  file=sys.stderr)
            time.sleep(interval)
    raise RuntimeError(f"ClickHouse not reachable after {timeout:.0f}s: {last_error}")


def run() -> None:
    from confluent_kafka import KafkaError

    # Conditions that are normal while waiting for spans, not real failures:
    #   UNKNOWN_TOPIC_OR_PART — topic not created yet (nothing produced so far)
    #   _PARTITION_EOF        — caught up to the end of a partition
    benign_errors = {KafkaError.UNKNOWN_TOPIC_OR_PART, KafkaError._PARTITION_EOF}

    consumer = build_consumer()
    consumer.subscribe([TOPIC])
    client = connect_clickhouse()

    batch: list[list] = []
    last_flush = time.monotonic()
    print(
        f"[argos-ingest] consuming '{TOPIC}' @ {BOOTSTRAP} "
        f"-> ClickHouse argos.spans (Ctrl+C to stop)"
    )

    try:
        while True:
            msg = consumer.poll(1.0)  # wait up to 1s for a message
            now = time.monotonic()

            if msg is not None:
                err = msg.error()
                if err is not None:
                    # Stay quiet on the normal "still waiting" conditions; only
                    # surface genuine errors.
                    if err.code() not in benign_errors:
                        print(f"[argos-ingest] kafka error: {err}", file=sys.stderr)
                else:
                    try:
                        span = json.loads(msg.value())
                        batch.append(span_dict_to_row(span))
                    except Exception as exc:  # noqa: BLE001 - skip poison messages
                        print(
                            f"[argos-ingest] skipping bad message: {exc}",
                            file=sys.stderr,
                        )

            # Flush when the batch is full OR enough time has elapsed.
            batch_full = len(batch) >= BATCH_MAX
            time_up = (now - last_flush) >= FLUSH_SECONDS
            if batch and (batch_full or time_up):
                _flush(client, consumer, batch)
                batch = []
                last_flush = now

    except KeyboardInterrupt:
        print("\n[argos-ingest] shutting down...")
    finally:
        # Don't lose whatever is still buffered on the way out.
        if batch:
            _flush(client, consumer, batch)
        consumer.close()


if __name__ == "__main__":
    run()
