# Argos — Distributed Tracing for Multi-Agent AI Systems

> The black box flight recorder for teams of AI agents. When several agents work
> together and something goes wrong, Argos reconstructs the entire step-by-step
> story across every agent and tool — so you can see *what* happened and *why*,
> not just that the final answer was wrong.

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)
![Status](https://img.shields.io/badge/status-early%20development-orange)
![Built with OpenTelemetry](https://img.shields.io/badge/built%20on-OpenTelemetry-7F77DD)

---

## Table of contents

1. [The problem in one paragraph](#1-the-problem-in-one-paragraph)
2. [What Argos actually is](#2-what-argos-actually-is)
3. [Who this is for](#3-who-this-is-for)
4. [What makes Argos different](#4-what-makes-argos-different)
5. [How it works — the big picture](#5-how-it-works--the-big-picture)
6. [The architecture, piece by piece](#6-the-architecture-piece-by-piece)
7. [The full tech stack and why each tool](#7-the-full-tech-stack-and-why-each-tool)
8. [Security — what we protect and how](#8-security--what-we-protect-and-how)
9. [What the end product looks like](#9-what-the-end-product-looks-like)
10. [Quickstart (the 10-minute promise)](#10-quickstart-the-10-minute-promise)
11. [The build plan — phase by phase](#11-the-build-plan--phase-by-phase)
12. [Project structure](#12-project-structure)
13. [Honest limitations](#13-honest-limitations)
14. [Contributing](#14-contributing)
15. [License](#15-license)

---

## 1. The problem in one paragraph

AI "agents" are programs that use a language model to make decisions and take
actions — calling tools, querying databases, and increasingly *handing work off
to other agents*. A single user request can now fan out across a research agent,
a search agent, three tools, and a summarizer agent. When the final answer comes
back wrong, you are blind: you see a bad result with no map of the dozen steps
that produced it. Existing tools trace a **single** agent's run well, but the
moment agents talk to *each other* — over emerging protocols like **MCP** (Model
Context Protocol) and **A2A** (Agent-to-Agent) — the story scatters across all of
them and nobody can reassemble it. That reassembly is the unsolved gap Argos fills.

---

## 2. What Argos actually is

Think of AI agents as employees in an office doing a task for you. They talk to
each other, use tools, and come back with an answer — but you can't *see* what
they did while working. Argos is the **security-camera-plus-logbook** for that
office. It does three simple jobs:

1. **Record** — sits beside the agents and writes down every step they take.
2. **Organize** — collects those scattered notes and stitches them into one
   clean, readable timeline.
3. **Show** — displays that timeline on a screen: who did what, in what order,
   how long it took, what it cost, and a red flag wherever something broke.

Everything technical in this document is just *how we do those three jobs well
enough that other people can run it too.*

The deeper truth that makes this a serious engineering project: **this is
distributed tracing** — the same battle-tested pattern Netflix and every major
cloud company uses to debug microservices — adapted to AI agents. The concept
transfers, but agents break the old assumptions (see
[Section 4](#4-what-makes-argos-different)).

---

## 3. Who this is for

The people who would **use** Argos are the same people who **hire** for the
roles it targets:

- **Platform / infrastructure engineers** putting agents into production and
  needing to keep them reliable.
- **SRE / DevOps teams** who get paged when an agent system misbehaves at 3 AM.
- **AI engineering teams** shipping multi-agent features who are currently
  "debugging blind."
- **FinOps / cost owners** who need to know which agent run burned $400 in
  tokens overnight.

If you are building anything where **more than one agent** cooperates, Argos is
for you.

---

## 4. What makes Argos different

Being honest: this space is **not** empty. Langfuse, LangSmith, Arize Phoenix,
Helicone, Datadog LLM Observability, and others already do agent observability,
and several are excellent. Argos does **not** try to beat them at their own game.
It owns a specific, underserved slice:

| Existing tools do this well | Argos focuses here instead |
|---|---|
| Trace a **single** agent's steps | Correlate traces across **many** agents |
| Generic LLM request/response logging | **Protocol-aware**: understands MCP tool calls + A2A handoffs |
| Treat cost as a summary metric | Track cost **per step**, flag runaway loops live |
| Security as an afterthought | **Secure by default** — secret redaction built in from step one |

**The one-sentence pitch:**

> Argos is an OpenTelemetry-native distributed tracing system that reconstructs
> the full execution of *multi-agent* AI systems communicating over MCP and A2A —
> surfacing causal failure chains, runaway loops, and per-run cost that
> single-agent tools miss.

**Why this is a standout portfolio piece, specifically:**

- It is built on a **proven cloud pattern** (distributed tracing), so every
  design decision is defensible in an interview.
- The hard part — correlating spans across independent agents — is a **distributed
  systems problem**, not an ML problem. That is the cloud/infra/networking lane.
- It uses the exact keywords cloud + platform JDs are screaming for in 2026:
  OpenTelemetry, Kafka, Kubernetes, ClickHouse, MCP, A2A, observability.
- Shipping it **open source, installable, and documented** turns it from "a
  student project" into "a tool people use" — a dramatically stronger signal.
- **Security as a headline feature** (redaction, encryption, least-privilege)
  differentiates it from the many agent tools that bolt security on later.

---

## 5. How it works — the big picture

One trace's journey, end to end:

```
  Someone's multi-agent app
  (research agent → search agent → tools → summarizer)
            │
            │  Argos SDK emits a "span" at every step
            │  (a span = one recorded action, tagged with a shared trace ID)
            ▼
   ┌─────────────────────────────────────────────┐
   │              ARGOS BACKEND (on K8s)          │
   │                                              │
   │   Kafka  ──►  Correlation  ──►  ClickHouse   │
   │  (buffer)      engine          (storage)     │
   │                  │                           │
   │                  ▼                           │
   │      Detect loops / failures / cost spikes   │
   │           → fire alerts (Prometheus)         │
   └─────────────────────────────────────────────┘
            │
            ▼
       Dashboard (Grafana / React)
   trace graph · cost-per-run · red alerts
   ← this is your demo video
```

**Read it as a story:** the SDK records → Kafka safely carries the flood of
records → the correlation engine reassembles them into one timeline → ClickHouse
files them away → the detection layer watches for trouble → the dashboard shows
a human the whole picture.

---

## 6. The architecture, piece by piece

### Piece 1 — The SDK (shipped as code, no UI)

A lightweight Python package a user installs with `pip install argos-sdk`. They
wrap their agents with it in **2–3 lines**, and it quietly emits an OpenTelemetry
span for every step: tool calls, agent-to-agent handoffs, LLM calls, decisions.
Each span carries the same **trace ID** so they can be reassembled later.

- **Lives:** inside the user's application.
- **Job:** record, and redact secrets *before* anything leaves the user's machine.
- **Design rule:** dead simple. If adoption takes 40 lines, the project fails.

### Piece 2 — The backend (runs on Kubernetes — the impressive part)

This is where the cloud-engineering proof lives. Three sub-components:

- **Kafka (ingestion buffer):** spans arrive here first. A burst of thousands of
  spans cannot crash the system; Kafka absorbs the flood and lets the rest of the
  pipeline consume at its own pace.
- **Correlation engine (the novel core):** pulls spans off Kafka, groups every
  span sharing a trace ID, and rebuilds the causal timeline — who called whom, in
  what order, where the chain broke. This is the part existing tools are weakest
  at for multi-agent flows.
- **ClickHouse (storage):** the assembled traces are stored in a column database
  built for exactly this — fast queries over huge volumes of trace/event data.

Alongside these runs a **detection layer** that scans incoming traces for trouble
patterns (runaway loops, repeated tool failures, cost spikes) and fires alerts.

### Piece 3 — The dashboard (the only website-like piece)

Reads from ClickHouse and draws the picture: the multi-agent trace as a visual
graph, cost-per-run, where loops/failures happened, and live alerts. Built on
Grafana first (fast, free, looks professional), with an optional custom React
view later.

> **Build priority:** the backend (Piece 2) is what gets you hired. The dashboard
> is the small visible tip — build it last, just polished enough for the demo.
> Do **not** sink weeks into a pretty UI.

---

## 7. The full tech stack and why each tool

| Layer | Tool | What it does here | Why this one |
|---|---|---|---|
| Instrumentation | **OpenTelemetry** + OpenInference | The standard format for emitting spans | Industry standard = interoperable, not lock-in |
| Language | **Python** | The SDK + engine logic | Dominant in the AI/agent ecosystem; you know it |
| Agents being traced | **AWS Bedrock / AgentCore**, **MCP**, **A2A** | The systems Argos observes | The hottest, most in-demand keywords of 2026 |
| Ingestion buffer | **Apache Kafka** | Absorbs the flood of incoming spans | The production standard for event streaming |
| Stream processing | Kafka consumers (Python) | Pull + enrich spans in flight | Keeps the pipeline decoupled and resilient |
| Correlation engine | **Python** (optionally **Go** later) | Stitches spans into causal traces | The core logic; Python first for speed of building |
| Storage | **ClickHouse** | Stores assembled traces, fast to query | Purpose-built for high-volume trace/event data |
| Metrics + alerting | **Prometheus** | Tracks cost, loop counts, failures; fires alerts | The de-facto cloud metrics standard |
| Dashboards | **Grafana** (+ optional React) | Visualizes traces, cost, alerts | Free, professional, pairs natively with Prometheus |
| Containerization | **Docker** | Packages every service | Universal; enables one-command setup |
| Orchestration | **Kubernetes** (+ **Helm**) | Runs the whole backend as a system | The core platform-engineering keyword |
| Infra as code | **Terraform** | Provisions cloud resources reproducibly | Your existing strength; #1 IaC tool |
| CI/CD | **GitHub Actions** | Tests + builds on every push | Proves the project is real and maintained |
| Cloud | **AWS** (EKS, MSK, S3) | Hosts the deployed version | Your certified platform |

**A note for interviews:** you do not strictly *need* Kafka and Kubernetes at a
student's data volume. You include them deliberately to demonstrate the
production-scale pattern. The senior move is to say exactly that: *"At my scale a
lighter queue would suffice; I used Kafka to demonstrate the pattern that scales
to production volume."* Knowing the tradeoff — not just the tool — is the signal.

---

## 8. Security — what we protect and how

The moment Argos records what agents do, it holds sensitive data: user questions,
private data the agents touched, API keys, customer info. Recording it means we
are responsible for protecting it. **This is a headline feature, not an
afterthought** — and it plays to a networking/security background.

What we build, from easiest to most involved:

1. **Secret redaction (the #1 expected feature).** The SDK automatically blanks
   out passwords, API keys, and tokens *before* a span ever leaves the user's
   machine — like a statement showing `****1234`. Pattern-based detection plus a
   user-configurable denylist.
2. **Encryption in transit.** All spans travel over **TLS/HTTPS** so nobody can
   eavesdrop between the agents and the backend.
3. **Encryption at rest.** The ClickHouse storage is encrypted (an AWS setting
   you enable), so stolen files are unreadable.
4. **Access control.** A login plus role-based permissions so only authorized
   people view traces. No open door to everyone's agent activity.
5. **Data retention limits.** Old traces auto-delete after a configurable window.
   Less data sitting around = less that can leak.

**What we deliberately do NOT promise:** enterprise compliance (SOC2, etc.),
formal security audits, or hardened multi-tenant isolation. That is out of scope
for an open-source learning project, and the README says so plainly (see
[Limitations](#13-honest-limitations)). Doing the *obvious* security well and
being *honest* about the rest is exactly what good engineers do.

---

## 9. What the end product looks like

When Argos is running, here is the experience — and the 90-second demo video that
goes on your LinkedIn and resume:

- **Scene 1 — setup (15s):** a multi-agent system runs on screen: a research
  agent delegating to a search agent and a summarizer, all over A2A, calling
  tools over MCP.
- **Scene 2 — normal run (20s):** you start a task. The dashboard lights up with
  a live trace graph — boxes per agent, arrows showing handoffs, tool calls
  branching off. Cost ticks up: `$0.04 … $0.07 … done`. Total: 8 steps, 2.3s,
  `$0.11`.
- **Scene 3 — the money shot (40s):** you inject a failure (make a tool return
  garbage). The two agents get stuck in a retry loop. On a normal tool you'd see
  only "task failed." On Argos, the trace graph turns **red at the exact failing
  span**, an alert fires (`runaway loop detected, 14 steps, cost spiking`), and
  you click straight to the step where it broke: *"step 3: search tool returned
  empty — everything after was doomed."*
- **Scene 4 — the punchline (15s):** *"This is the difference between knowing your
  agent failed and knowing why. Most tools show you the final output. Argos shows
  you the 14-step causal chain — across multiple agents and tools."*

That clip is the entire value of the project in 90 seconds: visual, a problem
people recognize, and real infrastructure working.

---

## 10. Quickstart (the 10-minute promise)

> Adoption dies if setup takes an afternoon. The whole stack must come up with
> one command, with a runnable example that produces a trace within minutes.

```bash
# 1. Clone
git clone https://github.com/<you>/argos.git
cd argos

# 2. Bring the whole backend up (Kafka, correlation engine, ClickHouse, Grafana)
docker compose up

# 3. In another terminal, run the bundled multi-agent example
cd examples/research-assistant
pip install -r requirements.txt
python run_demo.py

# 4. Open the dashboard — a trace should already be visible
open http://localhost:3000
```

Wrapping your *own* agents is the 2–3 line promise:

```python
from argos import trace_agents

# wrap your existing multi-agent app — that's it
with trace_agents(service="my-research-app"):
    result = my_agent_system.run("Summarize the latest on fusion energy")
```

---

## 11. The build plan — phase by phase

Built in dependency order, with open-source requirements baked in from day one
(not bolted on at the end). Each phase ends with something demonstrable.

### Phase 0 — Foundations
- Repo, Apache 2.0 license, README (this file), CONTRIBUTING.md, issue templates.
- `docker-compose.yml` skeleton; GitHub Actions running an empty test suite.
- **Outcome:** a clean public repo that looks intentional from commit one.

### Phase 1 — The SDK + a span
- Define what a "span" captures for an agent step (decision, tool call, handoff,
  cost, tokens).
- Build the OpenTelemetry-based SDK; emit spans from one simple agent.
- Add secret redaction from the start.
- **Outcome:** running an agent prints structured spans to the console.

### Phase 2 — Ingestion pipeline
- Stand up Kafka in Docker; SDK sends spans to it.
- A Python consumer reads spans off Kafka and writes raw spans to ClickHouse.
- **Outcome:** spans flow app → Kafka → ClickHouse and survive a restart.

### Phase 3 — The correlation engine (the core)
- Group spans by trace ID; reconstruct the causal timeline across **multiple**
  agents and MCP/A2A handoffs.
- Compute per-step and per-run cost.
- **Outcome:** a stored, queryable, fully-stitched multi-agent trace.

### Phase 4 — Detection + alerts
- Rules for runaway loops, repeated tool failures, and cost spikes.
- Export metrics to Prometheus; basic alerting.
- **Outcome:** injecting a failure produces a real alert.

### Phase 5 — The dashboard
- Grafana dashboards: trace graph, cost-per-run, alert panel.
- (Optional) a custom React trace-graph view if time allows.
- **Outcome:** the demo video from [Section 9](#9-what-the-end-product-looks-like)
  is recordable.

### Phase 6 — Make it genuinely usable (open-source polish)
- One-command `docker compose up`; a bundled, runnable example app.
- Real docs: quickstart, architecture, "wrap your own agents," troubleshooting.
- Kubernetes manifests + Helm chart; Terraform for an AWS (EKS) deploy.
- Versioned release, tests, green CI.
- **Outcome:** a stranger can clone, run, and trace their own agents in <10 min.

### Phase 7 — Open-source contributions along the way
- While building, you will hit real gaps in upstream projects (OpenLLMetry /
  Traceloop, OpenTelemetry GenAI semantic conventions, MCP spec repos). Fixing one
  and landing a merged PR is a resume line that beats almost any solo project,
  because it proves you can work in a real codebase.

---

## 12. Project structure

```
argos/
├── README.md                  # this file
├── LICENSE                    # Apache 2.0
├── CONTRIBUTING.md
├── docker-compose.yml         # one-command local stack
├── .github/
│   └── workflows/ci.yml       # tests + build on every push
├── sdk/                       # Piece 1 — the Python SDK
│   ├── argos/
│   │   ├── tracing.py         # span emission (OpenTelemetry)
│   │   ├── redaction.py       # secret blanking — security
│   │   └── protocols/         # MCP + A2A span adapters
│   └── tests/
├── backend/                   # Piece 2 — the pipeline
│   ├── ingest/                # Kafka consumers
│   ├── correlation/           # the causal-stitching engine
│   ├── detection/             # loop / failure / cost-spike rules
│   └── storage/               # ClickHouse schema + access
├── dashboard/                 # Piece 3 — Grafana configs (+ optional React)
├── examples/
│   └── research-assistant/    # bundled runnable multi-agent demo
├── deploy/
│   ├── helm/                  # Kubernetes Helm chart
│   └── terraform/             # AWS (EKS, MSK, S3) provisioning
└── docs/                      # quickstart, architecture, guides
```

---

## 13. Honest limitations

Argos is an open-source project built to demonstrate multi-agent observability.

- It includes **secret redaction** and **encryption**, but has **not** been
  security-audited for production use.
- It is **not** certified for any compliance regime (SOC2, HIPAA, etc.).
- It targets a focused set of scenarios (multi-agent flows over MCP/A2A) rather
  than every agent framework in existence.
- It prioritizes **depth on the multi-agent correlation problem** over broad
  feature coverage. Use at your own risk; contributions welcome.

This honesty is intentional. A small tool that does one hard thing well — and
says clearly what it does *not* do — is more credible than one that overpromises.

---

## 14. Contributing

Contributions are welcome — this project is built in public on purpose.

- Read `CONTRIBUTING.md` for setup and the dev workflow.
- Good first issues are labeled `good-first-issue`.
- Open an issue before large changes so we can align on direction.
- All contributions are under the Apache 2.0 license.

---

## 15. License

Licensed under the **Apache License 2.0**. See [`LICENSE`](LICENSE) for details.

---

> **Status:** early development, built in public as a learning project by an
> aspiring cloud / platform engineer. Watch the repo to follow along, and feel
> free to open an issue if multi-agent observability is your problem too.
