-- Argos ClickHouse schema — the `spans` table.
--
-- One row = one recorded agent step (see docs/PROJECT.md §05). The column set
-- mirrors the SDK's Span model (sdk/argos/span.py) field-for-field so a span can
-- be reassembled later by the correlation engine (Phase 3).
--
-- This file is mounted into ClickHouse's /docker-entrypoint-initdb.d/ by
-- docker-compose, so it runs automatically the first time the data volume is
-- empty — no manual setup step.

CREATE DATABASE IF NOT EXISTS argos;

CREATE TABLE IF NOT EXISTS argos.spans
(
    -- identity & causal structure
    trace_id        String,
    span_id         String,
    parent_span_id  Nullable(String),

    -- who emitted it. LowCardinality = a ClickHouse optimization for columns
    -- with few distinct values: stored as a dictionary, so they're small and
    -- fast to filter/group on.
    service_name    LowCardinality(String),
    agent_name      LowCardinality(String),
    step_type       LowCardinality(String),   -- llm_call|tool_call|a2a_handoff|decision
    name            String,

    -- timing. DateTime64(6) keeps microsecond precision; 'UTC' pins the zone so
    -- spans from agents in different timezones share one timeline.
    start_time      DateTime64(6, 'UTC'),
    end_time        Nullable(DateTime64(6, 'UTC')),
    duration_ms     Nullable(Float64),

    -- outcome
    status          LowCardinality(String),   -- ok|error
    error_message   Nullable(String),

    -- cost / usage
    model           Nullable(String),
    tokens_in       UInt32 DEFAULT 0,
    tokens_out      UInt32 DEFAULT 0,
    cost_usd        Float64 DEFAULT 0,

    -- extras & security. attributes is the JSON-serialized map (lossless,
    -- preserves nesting); query into it later with JSONExtract(...).
    attributes      String DEFAULT '{}',
    redacted        UInt8  DEFAULT 0,         -- ClickHouse has no native Bool; 0/1

    -- operational: when this row landed (not part of the Span model; a debugging
    -- aid so you can see ingestion lag).
    ingested_at     DateTime DEFAULT now()
)
ENGINE = MergeTree
-- Partition by day: ClickHouse stores each day's spans separately, which makes
-- time-range queries and dropping old data (retention, a later phase) cheap.
PARTITION BY toDate(start_time)
-- Sort key: optimized for the two queries Argos runs constantly —
-- "fetch every span in this trace" and "sum cost per run".
ORDER BY (trace_id, start_time);


-- The ASSEMBLED view of a trace (Phase 6 / Part B).
--
-- `spans` above is the raw, flat record of what happened. `trace_nodes` is the
-- correlation engine's *output* materialized for the dashboard: one row per span
-- but enriched with the engine-computed tree position — `depth` (how deep in the
-- parent->child tree) and `order_index` (pre-order position, so ORDER BY it
-- reproduces the tree top-to-bottom). The Grafana "Trace detail" panel reads this
-- to draw an indented timeline without re-deriving the tree in SQL.
--
-- ReplacingMergeTree(written_at): re-persisting the same trace replaces its rows
-- (dedup is by the sort key (trace_id, span_id), keeping the newest written_at).
-- Query with FINAL to collapse any not-yet-merged duplicates.
CREATE TABLE IF NOT EXISTS argos.trace_nodes
(
    trace_id        String,
    span_id         String,
    parent_span_id  Nullable(String),

    -- engine-computed tree position
    order_index     UInt32,                   -- pre-order rank; ORDER BY to draw the tree
    depth           UInt16,                   -- indentation level (root = 0)

    agent_name      LowCardinality(String),
    step_type       LowCardinality(String),
    name            String,
    duration_ms     Float64 DEFAULT 0,
    cost_usd        Float64 DEFAULT 0,
    status          LowCardinality(String),
    error_message   Nullable(String),
    attributes      String DEFAULT '{}',      -- JSON, same lossless encoding as spans
    orphaned        UInt8 DEFAULT 0,          -- the engine flagged a missing parent

    written_at      DateTime DEFAULT now()    -- ReplacingMergeTree version column
)
ENGINE = ReplacingMergeTree(written_at)
ORDER BY (trace_id, span_id);
