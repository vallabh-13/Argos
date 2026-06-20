"""CLI: detect trouble in a stored trace, print findings, optionally export metrics.

    python -m backend.detection <trace_id>            # one-shot: print findings
    python -m backend.detection <trace_id> --serve     # also expose /metrics, keep running

The one-shot form is the quick check; the ``--serve`` form is the Phase 4 demo:
evaluate a trace, push the numbers into Prometheus metrics, and keep the /metrics
endpoint up so the Prometheus container can scrape it. Thresholds come from the
ARGOS_LOOP_COUNT / ARGOS_FAILURE_COUNT / ARGOS_COST_LIMIT_USD env vars.
"""

from __future__ import annotations

import argparse
import json
import sys
import time

from ..correlation.engine import build_trace
from ..correlation.store import fetch_spans
from .engine import run_detection
from .models import DetectionConfig, Finding


def _print_findings(trace_id: str, findings: list[Finding]) -> None:
    if not findings:
        print(f"Trace {trace_id}: clean — no findings.")
        return
    print(f"Trace {trace_id}: {len(findings)} finding(s)")
    for f in findings:
        print(f"  [{f.severity.value.upper():8}] {f.rule}: {f.summary}")
        print(f"             spans: {f.span_ids}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m backend.detection")
    parser.add_argument("trace_id", help="trace_id to evaluate")
    parser.add_argument("--serve", action="store_true",
                        help="expose /metrics and keep running for Prometheus")
    parser.add_argument("--port", type=int, default=9108,
                        help="metrics port when --serve (default 9108)")
    parser.add_argument("--json", action="store_true", help="emit findings as JSON")
    args = parser.parse_args(argv)

    config = DetectionConfig.from_env()

    from backend.storage.clickhouse import get_client

    client = get_client()
    spans = fetch_spans(client, args.trace_id)
    if not spans:
        print(f"No spans found for trace_id {args.trace_id!r}.", file=sys.stderr)
        return 1

    trace = build_trace(spans, args.trace_id)
    findings = run_detection(trace, config)

    if args.json:
        print(json.dumps([f.to_dict() for f in findings], indent=2, default=str))
    else:
        _print_findings(args.trace_id, findings)

    if args.serve:
        # Import here so the one-shot path doesn't need prometheus_client.
        from .metrics import record, serve

        serve(args.port)
        record(trace, findings)
        print(f"\nServing metrics on :{args.port}/metrics — Ctrl-C to stop.")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            print("\nStopped.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
