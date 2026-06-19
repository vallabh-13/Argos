"""ClickHouse access for Argos.

Two responsibilities, deliberately separated:

* :func:`span_dict_to_row` — a **pure** function that turns a span dict (as
  produced by the SDK's ``Span.to_dict()`` and shipped over Kafka as JSON) into a
  row aligned to :data:`SPANS_COLUMNS`. No I/O, no ClickHouse import — so it's
  trivially unit-testable with no database running.

* :func:`get_client` / :func:`insert_spans` / :func:`query` — the thin I/O layer.
  The ``clickhouse-connect`` import is lazy (inside ``get_client``) so importing
  this module for the pure mapper doesn't require the driver installed.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any, Optional, Sequence

# Insert column order. Must match schema.sql. `ingested_at` is omitted on
# purpose — ClickHouse fills it via DEFAULT now().
SPANS_COLUMNS: tuple[str, ...] = (
    "trace_id",
    "span_id",
    "parent_span_id",
    "service_name",
    "agent_name",
    "step_type",
    "name",
    "start_time",
    "end_time",
    "duration_ms",
    "status",
    "error_message",
    "model",
    "tokens_in",
    "tokens_out",
    "cost_usd",
    "attributes",
    "redacted",
)


def _parse_dt(value: Optional[str]) -> Optional[datetime]:
    """ISO-8601 string -> timezone-aware datetime (or None).

    The SDK serializes timestamps with ``datetime.isoformat()``; ClickHouse's
    DateTime64 columns accept Python datetimes directly, so we parse back.
    """

    if value is None:
        return None
    return datetime.fromisoformat(value)


def span_dict_to_row(span: dict[str, Any]) -> list[Any]:
    """Map one span dict to a ClickHouse row (ordered per SPANS_COLUMNS).

    Type coercions worth noting:
      * timestamps: ISO string -> datetime
      * attributes: dict -> JSON string (the lossless, nest-preserving form)
      * redacted:   bool -> 0/1 (ClickHouse UInt8 has no native bool)
    """

    return [
        span["trace_id"],
        span["span_id"],
        span.get("parent_span_id"),
        span["service_name"],
        span["agent_name"],
        span["step_type"],
        span["name"],
        _parse_dt(span["start_time"]),
        _parse_dt(span.get("end_time")),
        span.get("duration_ms"),
        span["status"],
        span.get("error_message"),
        span.get("model"),
        int(span.get("tokens_in", 0)),
        int(span.get("tokens_out", 0)),
        float(span.get("cost_usd", 0.0)),
        json.dumps(span.get("attributes", {}), default=str),
        1 if span.get("redacted") else 0,
    ]


def get_client(
    host: Optional[str] = None,
    port: Optional[int] = None,
    database: Optional[str] = None,
    username: Optional[str] = None,
    password: Optional[str] = None,
):
    """Create a ClickHouse HTTP client.

    All connection settings read from the environment so the consumer is
    configured without code changes. The defaults below MUST match the
    credentials docker-compose.yml gives the ClickHouse container:

        CLICKHOUSE_HOST     (default "localhost")
        CLICKHOUSE_PORT     (default 8123)
        CLICKHOUSE_DB       (default "argos")
        CLICKHOUSE_USER     (default "argos")
        CLICKHOUSE_PASSWORD (default "argos")

    Explicit arguments win over env vars; env vars win over the defaults.
    """

    import clickhouse_connect  # lazy: only needed when actually talking to CH

    host = host or os.getenv("CLICKHOUSE_HOST", "localhost")
    port = port or int(os.getenv("CLICKHOUSE_PORT", "8123"))
    database = database or os.getenv("CLICKHOUSE_DB", "argos")
    username = username or os.getenv("CLICKHOUSE_USER", "argos")
    # Note the `is None` check: an empty-string password is a valid, intentional
    # value and must not be overridden by the default.
    if password is None:
        password = os.getenv("CLICKHOUSE_PASSWORD", "argos")

    return clickhouse_connect.get_client(
        host=host,
        port=port,
        database=database,
        username=username,
        password=password,
    )


def insert_spans(client, rows: Sequence[Sequence[Any]]) -> None:
    """Batch-insert span rows. Each row must follow SPANS_COLUMNS order."""

    if not rows:
        return
    client.insert(
        "argos.spans",
        list(rows),
        column_names=list(SPANS_COLUMNS),
    )


def query(client, sql: str):
    """Run a SQL query and return the result rows (used by verification/tests)."""

    return client.query(sql).result_rows
