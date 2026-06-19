"""Argos storage layer — ClickHouse access and the span row mapping."""

from .clickhouse import (
    SPANS_COLUMNS,
    get_client,
    insert_spans,
    query,
    span_dict_to_row,
)

__all__ = [
    "SPANS_COLUMNS",
    "get_client",
    "insert_spans",
    "query",
    "span_dict_to_row",
]
