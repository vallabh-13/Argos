"""CLI: detect trouble in stored traces, print findings, export Prometheus metrics.

Three ways to run it:

    python -m backend.detection <trace_id>            # one-shot: print findings
    python -m backend.detection <trace_id> --serve     # one-shot + expose /metrics, stay up
    python -m backend.detection --watch                # poll for NEW traces, keep scoring

The ``--watch`` form is the Phase 5 demo driver: it serves /metrics and, every
few seconds, evaluates any trace_id it hasn't seen yet. So once it's running you
just emit a misbehaving trace (``python examples/emit_bad_trace.py``) and the
dashboard lights up on its own — no need to re-run anything.

Thresholds come from the ARGOS_LOOP_COUNT / ARGOS_FAILURE_COUNT /
ARGOS_COST_LIMIT_USD env vars (see DetectionConfig.from_env).
"""

from __future__ import annotations

import argparse
import json
import sys
import time

from ..correlation.engine import build_trace
from ..correlation.store import fetch_spans, recent_trace_ids
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


def _evaluate(client, trace_id: str, config: DetectionConfig):
    """Fetch -> assemble -> detect for one trace. Returns (trace, findings)."""

    spans = fetch_spans(client, trace_id)
    if not spans:
        return None, []
    trace = build_trace(spans, trace_id)
    return trace, run_detection(trace, config)


def _watch(client, config: DetectionConfig, port: int, interval: float) -> None:
    """Serve /metrics and score every newly-seen trace until interrupted."""

    from .metrics import init_series, record, serve
    from ..correlation.persist import ensure_trace_nodes_table, write_trace_nodes

    serve(port)
    init_series()  # show a calm green 0 before anything fires
    ensure_trace_nodes_table(client)  # create the detail table if it's missing

    # Treat traces that already exist as "seen", so the demo starts calm and only
    # reacts to traces emitted AFTER the watcher starts.
    seen: set[str] = set(recent_trace_ids(client, limit=500))
    print(f"Watching for new traces — serving metrics on :{port}/metrics.")
    print(f"({len(seen)} existing trace(s) ignored; Ctrl-C to stop.)")

    try:
        while True:
            for trace_id in recent_trace_ids(client, limit=50):
                if trace_id in seen:
                    continue
                seen.add(trace_id)
                trace, findings = _evaluate(client, trace_id, config)
                if trace is None:
                    continue
                record(trace, findings)
                # Materialize the assembled tree for the Grafana detail panel.
                # Best-effort: a persistence hiccup shouldn't stop the watcher.
                try:
                    write_trace_nodes(client, trace)
                except Exception as exc:  # noqa: BLE001
                    print(f"  (could not persist trace_nodes for {trace_id}: {exc})")
                _print_findings(trace_id, findings)
            time.sleep(interval)
    except KeyboardInterrupt:
        print("\nStopped.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m backend.detection")
    parser.add_argument("trace_id", nargs="?", help="trace_id to evaluate (one-shot)")
    parser.add_argument("--watch", action="store_true",
                        help="poll for new traces and keep scoring (demo driver)")
    parser.add_argument("--serve", action="store_true",
                        help="expose /metrics and keep running (one-shot mode)")
    parser.add_argument("--port", type=int, default=9108,
                        help="metrics port for --serve/--watch (default 9108)")
    parser.add_argument("--interval", type=float, default=3.0,
                        help="--watch poll interval in seconds (default 3)")
    parser.add_argument("--json", action="store_true", help="emit findings as JSON")
    args = parser.parse_args(argv)

    if not args.watch and not args.trace_id:
        parser.error("give a trace_id, or use --watch")

    config = DetectionConfig.from_env()

    from backend.storage.clickhouse import get_client

    client = get_client()

    if args.watch:
        _watch(client, config, args.port, args.interval)
        return 0

    trace, findings = _evaluate(client, args.trace_id, config)
    if trace is None:
        print(f"No spans found for trace_id {args.trace_id!r}.", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps([f.to_dict() for f in findings], indent=2, default=str))
    else:
        _print_findings(args.trace_id, findings)

    if args.serve:
        from .metrics import init_series, record, serve

        serve(args.port)
        init_series()
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
