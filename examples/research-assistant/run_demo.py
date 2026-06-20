"""Argos demo — three real agents on AWS Bedrock, traced end to end.

    orchestrator --(A2A)--> search --(MCP tool)--> ... --(A2A)--> summarizer

Every step is wrapped with the Argos SDK, so real spans flow through the pipeline
(Kafka -> ClickHouse -> correlation -> detection). The agents exist only to give
Argos something genuine to trace.

Setup (once):
    pip install -e "sdk[kafka]"
    pip install -r examples/research-assistant/requirements.txt
    cp examples/research-assistant/.env.example examples/research-assistant/.env
    # edit .env: AWS creds + model, OR set ARGOS_BEDROCK_MOCK=1 to skip AWS

Run to the console (no pipeline needed):
    python examples/research-assistant/run_demo.py --scenario happy

Run into the live pipeline (after `docker compose up -d`):
    # set ARGOS_KAFKA_BOOTSTRAP=localhost:29092 in .env (or the shell)
    python examples/research-assistant/run_demo.py --scenario happy

Scenarios:
    --scenario happy   a clean run that succeeds end to end
    --scenario fail    the tool returns garbage; the search agent really loops
                       and retries, tripping the Phase 4 detectors (see step 3)
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# Make the demo's sibling modules importable no matter where this is launched from.
sys.path.insert(0, str(Path(__file__).parent))

from argos import init_tracing  # noqa: E402

from bedrock_client import load_env, make_llm  # noqa: E402
from agents.orchestrator import orchestrate  # noqa: E402

DEFAULT_QUESTION = "What is fusion energy and why does it matter?"


def main(argv: list[str] | None = None) -> int:
    load_env()  # pull .env in before we read ARGOS_KAFKA_BOOTSTRAP etc.

    parser = argparse.ArgumentParser(prog="run_demo.py", description="Argos multi-agent demo")
    parser.add_argument("--scenario", choices=["happy", "fail"], default="happy",
                        help="happy = clean run; fail = tool returns garbage and agents loop")
    parser.add_argument("--question", default=DEFAULT_QUESTION, help="the research question")
    parser.add_argument("--sink", choices=["auto", "console", "kafka"], default="auto",
                        help="where spans go (auto = kafka if ARGOS_KAFKA_BOOTSTRAP set)")
    args = parser.parse_args(argv)

    bootstrap = os.getenv("ARGOS_KAFKA_BOOTSTRAP")
    use_kafka = args.sink == "kafka" or (args.sink == "auto" and bootstrap)

    if use_kafka:
        if not bootstrap:
            parser.error("--sink kafka needs ARGOS_KAFKA_BOOTSTRAP set")
        from argos import KafkaSink

        init_tracing(service="research-assistant", sink=KafkaSink(bootstrap_servers=bootstrap))
        print(f"=== Argos demo - publishing spans to Kafka @ {bootstrap} ===")
    else:
        init_tracing(service="research-assistant")
        print("=== Argos demo - emitting spans to the console ===")

    llm = make_llm()
    print(f"LLM: {llm.describe()}")
    print(f"Scenario: {args.scenario} | Question: {args.question}\n")

    answer = orchestrate(llm, args.question, scenario=args.scenario)

    print("\n=== Final answer ===")
    print(answer)

    if use_kafka:
        print(
            "\nSpans published to 'argos.spans'. With the consumer + detector running, "
            "this run will appear in ClickHouse and on the Grafana dashboard."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
