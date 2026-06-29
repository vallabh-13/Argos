# CLAUDE.md — Project context for Claude Code

> Claude Code reads this automatically at the start of every session.
> Keep it tight (50–150 lines). The README.md is the real source of truth;
> docs/PROJECT.md is the distilled plan. Link, don't duplicate.

## What Argos is (read this first)
Argos is **OpenTelemetry-native distributed tracing for multi-agent AI systems**.
When several AI agents work together (talking over A2A, calling tools over MCP)
and the result is wrong, Argos reconstructs the full step-by-step causal timeline
across every agent and tool — surfacing failure chains, runaway loops, and
per-run cost that single-agent tools miss.

**Critical framing — do not get this wrong:**
- The **product** is the tracing system (SDK + backend pipeline + dashboard).
- The **agents are NOT the product.** `examples/research-assistant/` is a small
  bundled multi-agent demo that exists ONLY so Argos has something to trace.
- There is **no user-facing "create an agent" app.** Don't build one.

## Stack (don't deviate without asking)
- Language: **Python** (SDK + correlation engine + consumers)
- Instrumentation: **OpenTelemetry** (+ OpenInference conventions)
- Demo agents (the things being traced): **AWS Bedrock / AgentCore**, **MCP**, **A2A**
- Ingestion buffer: **Apache Kafka**
- Storage: **ClickHouse**
- Metrics + alerting: **Prometheus**
- Dashboards: **Grafana** first (optional custom React later — build last)
- Containers: **Docker** (one-command `docker compose up`)
- Orchestration: **Kubernetes** + **Helm**
- Infra as code: **Terraform**
- CI/CD: **GitHub Actions**
- Cloud: **AWS** (EKS, MSK, S3)
- License: **Apache 2.0**

## Repo layout (see README §12)
- `sdk/argos/` — Python SDK: `tracing.py` (span emission), `redaction.py` (secret blanking), `protocols/` (MCP + A2A adapters)
- `backend/ingest/` — Kafka consumers
- `backend/correlation/` — the causal-stitching engine (the core)
- `backend/detection/` — loop / failure / cost-spike rules
- `backend/storage/` — ClickHouse schema + access
- `examples/research-assistant/` — the bundled multi-agent demo
- `deploy/` — Grafana + Prometheus configs for the `docker compose` stack
- `docs/` — quickstart, architecture, guides
- `scripts/` — `bootstrap.py` + `setup`/`setup.cmd` (fresh-clone setup)

## Non-negotiable rules
- **Security first:** the SDK must redact secrets (API keys, tokens, passwords)
  *before* a span leaves the user's machine. Never log or store raw secrets.
- **Adoption simplicity:** wrapping someone's agents must stay 2–3 lines. If it
  takes 40 lines, the design failed.
- Every span carries a shared **trace_id** so spans can be reassembled later.
- Open-source hygiene from day one: Apache 2.0, tests, green CI on every push.

## How to work with me
- I'm LEARNING (aspiring cloud/platform engineer). After writing code, explain
  what it does and WHY in plain language.
- Build **one phase at a time** (see docs/PROJECT.md → Implementation Plan).
  Stop and let me run/test before moving to the next phase.
- Each phase must end in something demonstrable.
- Show me the plan before large multi-file changes. Ask instead of guessing.
- Prefer the simplest thing that proves the pattern; note tradeoffs out loud
  (e.g. "Kafka is overkill at this volume — included to show the prod pattern").

## Current status
🔧 Update as you go.
- Phase 0 — Foundations: done.
- Phase 1 — SDK + a span: done.
- Phase 2 — ingestion pipeline: done (live round-trip + restart verified).
- Phase 3 — correlation engine: done (pure stitching engine, per-run rollup,
  ClickHouse reader, `python -m backend.correlation <trace_id>` CLI; 7 unit tests
  + 1 guarded integration test).
- Phase 4 — detection + alerts: done (loop / tool-failure / cost-spike rules,
  configurable thresholds, structured findings, Prometheus exporter +
  prometheus service in docker-compose, `python -m backend.detection <trace_id>
  [--serve]` CLI; rule + boundary + metrics tests). Verified live: a CRITICAL
  Finding fired end-to-end and `argos_findings_total` was scraped by Prometheus.
- Phase 5 — Grafana dashboard: done (grafana service auto-provisioned via
  deploy/grafana/; Prometheus + ClickHouse datasources; stat/gauge/timeseries/
  table panels that go red on findings; `--watch` detector mode so emitting a bad
  trace lights the dashboard unaided; recording guide in docs/demo.md).
