"""Argos demo — three real agents on AWS Bedrock, traced end to end.

    orchestrator --(A2A)--> search --(MCP tool)--> ... --(A2A)--> summarizer

Every step is wrapped with the Argos SDK, so real spans flow through the pipeline
(Kafka -> ClickHouse -> correlation -> detection). The agents exist only to give
Argos something genuine to trace.

Setup (once):
    pip install -e "sdk[kafka]"
    pip install -r examples/research-assistant/requirements.txt
    cp examples/research-assistant/.env.example examples/research-assistant/.env
    aws configure   # once — boto3 reads creds from the AWS credential chain
    # edit .env: set the model, OR set ARGOS_BEDROCK_MOCK=1 to skip AWS entirely

Run to the console (no pipeline needed):
    python examples/research-assistant/run_demo.py --scenario happy

Run into the live pipeline (after `docker compose up -d`):
    # set `backend: localhost:29092` in argos.config.yml — that's all init_tracing()
    # needs; this demo calls it with NO arguments and reads service + backend from
    # that one file. Force a sink with --sink console / --sink kafka if you like.
    python examples/research-assistant/run_demo.py --scenario happy

Scenarios:
    --scenario happy   a clean run that succeeds end to end
    --scenario fail    the tool returns garbage; the search agent really loops
                       and retries, tripping the Phase 4 detectors (see step 3)
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Make the demo's sibling modules importable no matter where this is launched from.
sys.path.insert(0, str(Path(__file__).parent))

from argos import console_sink, init_tracing, load_config  # noqa: E402

from bedrock_client import load_env, make_llm  # noqa: E402
from agents.orchestrator import orchestrate  # noqa: E402

DEFAULT_QUESTION = "What is fusion energy and why does it matter?"


def main(argv: list[str] | None = None) -> int:
    load_env()  # pull .env in for ARGOS_BEDROCK_MOCK / ARGOS_DEMO_MAX_RETRIES etc.

    parser = argparse.ArgumentParser(prog="run_demo.py", description="Argos multi-agent demo")
    parser.add_argument("--scenario", choices=["happy", "fail"], default="happy",
                        help="happy = clean run; fail = tool returns garbage and agents loop")
    parser.add_argument("--question", default=DEFAULT_QUESTION, help="the research question")
    parser.add_argument("--sink", choices=["auto", "console", "kafka"], default="auto",
                        help="where spans go (auto = follow argos.config.yml 'backend')")
    args = parser.parse_args(argv)

    # The whole point of the single-file flow: service name and span backend both
    # come from argos.config.yml. We read it here only to (a) decide the closing
    # message and (b) validate `--sink kafka`. Selecting the sink itself is left to
    # init_tracing() so the demo genuinely exercises the no-arg path.
    config = load_config()
    backend = config.backend if config.has_backend else None

    if args.sink == "console":
        # Force console even if the config names a backend; service still from config.
        init_tracing(sink=console_sink)
        use_kafka = False
        print("=== Argos demo - emitting spans to the console (forced via --sink console) ===")
    elif args.sink == "kafka":
        if not backend:
            parser.error("--sink kafka needs a 'backend' in argos.config.yml")
        init_tracing()  # no-arg: builds the Kafka sink from the config backend
        use_kafka = True
        print(f"=== Argos demo - publishing spans to Kafka @ {backend} (from argos.config.yml) ===")
    else:  # auto — the single-file flow, init_tracing() with NO arguments
        init_tracing()
        use_kafka = backend is not None
        if use_kafka:
            print(f"=== Argos demo - publishing spans to Kafka @ {backend} (from argos.config.yml) ===")
        else:
            print("=== Argos demo - emitting spans to the console (no 'backend' in argos.config.yml) ===")

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
