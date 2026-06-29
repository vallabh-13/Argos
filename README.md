# Argos

> Argos is a distributed tracing system for AI applications where several agents work together. When a team of agents returns a wrong answer, Argos rebuilds the full story across every agent and every tool, so you can see what happened and why, not only that the final result was wrong.

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)
![Status](https://img.shields.io/badge/status-working%20demo-brightgreen)
![Built on OpenTelemetry](https://img.shields.io/badge/built%20on-OpenTelemetry-7F77DD)
![Python](https://img.shields.io/badge/python-3.10%2B-3776AB)

**Status.** This is a working demo, built in public as a learning project by an aspiring cloud and platform engineer. The local stack runs end to end with one command. It is not production hardened, and the roadmap below is honest about what is done and what is still planned.

![Argos architecture](docs/images/architecture.png)

## Table of contents

1. [Overview](#1-overview)
2. [The problem](#2-the-problem)
3. [What Argos does](#3-what-argos-does)
4. [Architecture](#4-architecture)
5. [How a trace flows](#5-how-a-trace-flows)
6. [Components](#6-components)
7. [Technology stack](#7-technology-stack)
8. [Security](#8-security)
9. [Quickstart](#9-quickstart)
10. [Instrumenting your own agents](#10-instrumenting-your-own-agents)
11. [What the dashboard shows](#11-what-the-dashboard-shows)
12. [Project status and roadmap](#12-project-status-and-roadmap)
13. [Project structure](#13-project-structure)
14. [Limitations](#14-limitations)
15. [Contributing](#15-contributing)
16. [License](#16-license)

## 1. Overview

Modern AI applications no longer run as a single model call. A user request can fan out across an orchestrator agent, a search agent, several tools, and a summarizer, and increasingly these agents hand work to one another over open protocols such as MCP (Model Context Protocol) and A2A (Agent to Agent). When the final answer is wrong, you are left with the answer and nothing else. You cannot see the dozen steps that produced it.

Argos applies distributed tracing, the same proven pattern that large cloud companies use to debug microservices, to teams of cooperating agents. It records every step each agent takes, stitches those scattered records into one causal timeline keyed by a shared trace id, stores them for fast querying, and shows the whole picture on a dashboard with cost per run and clear flags wherever something broke.

The interesting engineering is not machine learning. It is a distributed systems problem: correlating events that arrive out of order, from independent processes, into a single coherent story. That is the platform and infrastructure lane, and it is what this project is built to demonstrate and to teach me.

## 2. The problem

Tools that observe a single agent already exist and several are excellent. The gap appears the moment agents talk to each other. The steps of one user request scatter across multiple agents and tools, each emitting its own logs, and nobody reassembles them into one timeline. You can see that agent A failed, or that tool B returned nothing, but you cannot see that tool B returned nothing on step three and that everything after step three was doomed because of it.

Argos focuses on that reassembly. It treats a run as a tree of steps that share one trace id, rebuilds the parent and child relationships even when records arrive in any order, and surfaces the failure chain across agent boundaries.

## 3. What Argos does

Think of a team of agents as employees in an office working on a task for you. They talk to each other, use tools, and come back with an answer, but you cannot watch them while they work. Argos is the security camera and the logbook for that office, and it does three jobs.

1. Record. A small SDK sits beside your agents and writes down every step they take.
2. Organize. A backend pipeline collects those scattered records and stitches them into one clean timeline.
3. Show. A dashboard displays that timeline: who did what, in what order, how long it took, what it cost, and a red flag wherever something broke.

Everything technical in this document is simply how those three jobs are done well enough that someone else can run it too.

## 4. Architecture

The system has three layers: an SDK that lives inside your application, a backend pipeline that runs as containers, and a dashboard. The diagram below shows how a span travels from your agents all the way to Grafana.

```mermaid
flowchart LR
    subgraph app["Your multiagent application"]
        direction TB
        o[Orchestrator agent] --> s[Search agent]
        s --> sum[Summarizer agent]
        s -->|tool call over MCP| tool[Web search tool]
    end

    app -->|Argos SDK emits<br/>one span per step| kafka[(Kafka topic<br/>argos.spans)]
    kafka --> consumer[Ingest consumer]
    consumer --> ch[(ClickHouse)]
    ch --> watch[Correlation and detection<br/>watch loop]
    watch -->|assembled trace tree| ch
    watch -->|findings as metrics| prom[(Prometheus)]
    ch --> grafana[Grafana dashboard]
    prom --> grafana
```

Read it as a story. The SDK records each step and publishes it to Kafka. Kafka safely carries the flood of records. The ingest consumer drains Kafka into ClickHouse. The watch loop reads spans, reconstructs the causal tree, writes the assembled trace back to ClickHouse, and reports any findings to Prometheus. Grafana reads from ClickHouse and Prometheus and draws the picture for a human.

## 5. How a trace flows

This is the failure scenario from the bundled demo, where a tool keeps returning garbage and the search agent retries until it hits a safe cap. Every step is recorded under one shared trace id, so Argos can later show the exact point where the run went wrong.

```mermaid
sequenceDiagram
    participant U as User
    participant O as Orchestrator
    participant S as Search agent
    participant T as Web search tool
    participant SU as Summarizer
    U->>O: ask a question
    O->>S: hand off the task over A2A
    loop retry until the cap
        S->>T: call the tool over MCP
        T-->>S: malformed result
    end
    S->>SU: hand off the findings
    SU-->>O: degraded answer
    Note over O,SU: Argos records every step under one trace id
```

In a normal tool you would see only that the task failed. In Argos the retry loop shows up as a repeating run of failing tool calls, the detection rules flag a runaway loop and a repeated tool failure, and you can click straight to the step where it broke.

## 6. Components

**The SDK.** A small Python package that lives inside your application. You wrap each agent step in a context manager and it emits one OpenTelemetry span per step, tagged with a shared trace id, covering tool calls, agent handoffs, model calls, and decisions. The whole point is that adoption stays two or three lines of code. It also redacts secrets before any span leaves your machine.

**The backend pipeline.** This is where the cloud engineering lives. Kafka is the ingestion buffer, so a burst of thousands of spans cannot overwhelm the system. The ingest consumer drains Kafka into ClickHouse using at least once delivery, so a restart never loses a span. The correlation engine groups every span that shares a trace id and rebuilds the causal tree, which is the part that single agent tools handle weakest. The detection layer scans assembled traces for runaway loops, repeated tool failures, and cost spikes, and exposes the results as Prometheus metrics.

**The dashboard.** Grafana, provisioned automatically from the repo. It reads from ClickHouse and Prometheus and draws the trace as a readable timeline, with cost per run and panels that turn red when a finding fires. The dashboard is the small visible tip of the system. The backend is the substantial part.

## 7. Technology stack

Each choice is deliberate, and for several of them a lighter option would be enough at demo volume. They are included to practice the pattern that scales to production.

- Instrumentation: OpenTelemetry with OpenInference conventions. The industry standard format for emitting spans, so traces stay interoperable.
- Language: Python for the SDK, the correlation engine, and the consumers.
- Agents traced in the demo: AWS Bedrock running Claude Haiku 4.5, communicating over MCP and A2A.
- Ingestion buffer: Apache Kafka, the standard for event streaming.
- Storage: ClickHouse, a columnar database designed for fast queries over very large volumes of trace data.
- Metrics and alerting: Prometheus, the common cloud metrics standard.
- Dashboards: Grafana, free, professional, and a natural pair with Prometheus and ClickHouse.
- Containers: Docker, so the whole stack starts with one command.
- Orchestration: Kubernetes with Helm, planned for the cloud deployment phase.
- Infrastructure as code: Terraform, planned for the AWS deployment phase.
- Continuous integration: GitHub Actions runs the test suite on every push.
- Cloud target: AWS using EKS, MSK, and S3, planned.

A note for interviews. You do not strictly need Kafka or Kubernetes at this data volume. They are included on purpose to demonstrate the production pattern. The senior framing is to say exactly that: at this scale a lighter queue would be enough, and Kafka is here to show the design that scales to production volume. Knowing the tradeoff, not only the tool, is the signal.

## 8. Security

The moment Argos records what agents do, it holds sensitive data: user questions, the data agents touched, API keys, and customer information. Recording it makes protecting it a responsibility, so security is treated as a feature rather than an afterthought.

- Secret redaction. The SDK automatically blanks passwords, API keys, and tokens before a span ever leaves your machine. It uses both a denylist of sensitive key names and pattern matching on values, and the demo plants a fake secret on a span to prove the redaction runs on real traces.
- Secrets never live in committed files. AWS credentials come from the standard credential chain, either through aws configure locally or an attached role in the cloud. The config file holds only non secret settings, and the real config is ignored by git.
- Planned hardening. Encryption in transit over TLS, encryption at rest in storage, access control with roles, and configurable data retention are on the roadmap, framed honestly as planned rather than shipped.

What this project does not claim: compliance certification, a formal security audit, or hardened isolation between tenants. Doing the obvious security well and being honest about the rest is the intent.

## 9. Quickstart

The whole stack comes up with one Docker command, and a guided console walks you from a fresh clone to a live trace.

```bash
# 1. Clone
git clone https://github.com/<you>/argos.git
cd argos

# 2. Create and activate a virtual environment
python -m venv .venv
source .venv/bin/activate          # Windows PowerShell: .venv\Scripts\Activate.ps1

# 3. Install everything: the SDK, the backend pipeline, and the demo
pip install -r requirements-all.txt

# 4. Check your machine has the prerequisites
python -m argos setup

# 5. Open the guided console and follow the numbered steps
python -m argos
```

On a brand new clone you can skip steps 3 and 4 and run the bootstrap, which installs the dependencies for you and then runs the checker.

```bash
python scripts/bootstrap.py     # or  ./scripts/setup  on macOS and Linux,  scripts\setup.cmd  on Windows
```

The guided console gives you a numbered menu.

```
1) Connect AWS          run aws configure (optional, the demo also runs in mock mode)
2) Start backend        docker compose up, health checked, plus the ingest consumer and detector
3) Instrument my agents the one code change for your own app (writes argos.config.yml)
4) Run demo             emit real spans through the pipeline (happy or fail)
5) Open dashboard       Grafana at http://localhost:3000
6) Settings             view argos.config.yml
```

For the fastest path to a visible trace, choose 2, then 4, then 5. Start the backend, run the demo so spans actually flow, then open the dashboard. The dashboard is empty until spans arrive, so there is nothing to show until you run the demo or your own instrumented app. The first run of option 2 also creates argos.config.yml for you if it is missing, and it starts both the ingest consumer and the detector in the background so the dashboard fills in end to end with no extra terminals.

On Windows, if python is not on your PATH, use the py launcher instead, for example py -m venv .venv and py -m argos. The bundled scripts\setup.cmd already prefers py.

## 10. Instrumenting your own agents

Two imports, one startup call, and a context manager around each step you want to trace.

```python
from argos import init_tracing, trace_step

init_tracing()                         # reads service_name and backend from argos.config.yml

with trace_step(agent_name="search", step_type="tool_call", name="web.search") as step:
    result = call_my_tool(query)
    step.set_usage(model="...", tokens_in=120, tokens_out=80)   # optional, for cost
```

init_tracing takes no arguments. It reads the service name and the span backend from argos.config.yml, and menu option 3 writes that file for you and prints these exact snippets with your own values filled in. With no backend set, spans print to the console. Set the backend to localhost:29092 to pipe them into the pipeline. The step type is one of llm_call, tool_call, a2a_handoff, or decision.

## 11. What the dashboard shows

When the backend is running and a trace has flowed through, the Grafana dashboard shows the run as a readable timeline. Each step appears in sequence with its agent, its type, its duration, and its status. Cost is rolled up per run. Panels turn red when the detection rules fire, for example when a tool fails repeatedly or a run loops past its threshold, and you can drill into the exact step where the chain broke.

The architecture and flow diagrams above render directly on GitHub. To show the live dashboard, save a screenshot of your own running stack to docs/images/dashboard.png and uncomment the line below.

<!-- ![Argos dashboard](docs/images/dashboard.png) -->

## 12. Project status and roadmap

Built in dependency order, so each phase ends with something you can run and show.

Done and working in the local demo:

- Phase 0, foundations. Repository, Apache 2.0 license, issue templates, the Docker stack skeleton, and GitHub Actions running the tests.
- Phase 1, the SDK and a span. The OpenTelemetry based SDK emits structured spans for agent steps, with secret redaction from the start.
- Phase 2, the ingestion pipeline. Spans travel from the app through Kafka to ClickHouse and survive a restart.
- Phase 3, the correlation engine. Spans are grouped by trace id and stitched into a causal timeline across multiple agents and handoffs, with cost rolled up per run.
- Phase 4, detection and alerts. Rules for runaway loops, repeated tool failures, and cost spikes export metrics to Prometheus.
- Phase 5, the dashboard. Grafana is provisioned automatically, with panels that go red when a finding fires.

In progress:

- Phase 6, make it genuinely usable. A bundled multiple agent demo on AWS Bedrock, a richer trace detail view, one command startup, and the guided console that brings up the whole stack including the consumer and the detector.

Planned:

- Phase 7, cloud deployment. Kubernetes manifests with a Helm chart, and Terraform for an AWS deployment.
- Ongoing, upstream contributions. Fixing real gaps in the surrounding open source projects as they come up.

## 13. Project structure

```
argos/
  README.md                   this file
  LICENSE                     Apache 2.0
  CLAUDE.md                   project context for the AI pair programmer
  docker-compose.yml          one command local stack
  argos.config.example.yml    copy to argos.config.yml to configure
  requirements-all.txt        install the whole stack in one command
  conftest.py                 test path setup
  scripts/                    bootstrap.py and the setup launchers
  .github/
    workflows/ci.yml          tests and build on every push
    CONTRIBUTING.md
  sdk/                        the Python SDK
    argos/
      tracing.py              span emission on OpenTelemetry
      redaction.py            secret blanking, security
      sinks.py                console and Kafka outputs
      config.py               the single config file reader
      protocols/              MCP and A2A span adapters
      cli/                    the guided console and setup checker
    tests/
  backend/                    the pipeline
    ingest/                   Kafka consumer
    correlation/              the causal stitching engine
    detection/                loop, failure, and cost rules
    storage/                  ClickHouse schema and access
    tests/
  examples/
    emit_bad_trace.py         emit a deliberately broken trace
    research-assistant/       bundled runnable multiple agent demo
  deploy/                     configs for the docker compose stack
    grafana/                  dashboards and datasource provisioning
    prometheus/               scrape config
  docs/                       quickstart, architecture, and guides
    images/                   screenshots and diagrams
```

## 14. Limitations

Argos is an open source project built to demonstrate and to learn multiple agent observability.

- It includes secret redaction, but it has not been security audited for production use.
- It is not certified for any compliance regime such as SOC2 or HIPAA.
- It targets a focused set of scenarios, agents that cooperate over MCP and A2A, rather than every agent framework in existence.
- It prioritizes depth on the correlation problem over broad feature coverage.

This honesty is intentional. A small tool that does one hard thing well, and says clearly what it does not do, is more credible than one that overpromises.

## 15. Contributing

Contributions are welcome. This project is built in public on purpose.

- Read [.github/CONTRIBUTING.md](.github/CONTRIBUTING.md) for setup and the development workflow.
- Good first issues are labeled good first issue.
- Open an issue before large changes so we can agree on direction.
- All contributions are under the Apache 2.0 license.

## 16. License

Licensed under the Apache License 2.0. See [LICENSE](LICENSE) for details.
