"""CLI: assemble and print a stored trace.

    python -m backend.correlation <trace_id>          # pretty tree + summary
    python -m backend.correlation <trace_id> --json    # machine-readable JSON

This is the Phase 3 "demonstrable" bit: pull a trace's spans out of ClickHouse,
stitch them, and show the reconstructed multi-agent timeline with per-run cost.
Connection settings come from the same CLICKHOUSE_* env vars the consumer uses.
"""

from __future__ import annotations

import argparse
import json
import sys

from .engine import build_trace
from .models import AssembledTrace, TraceNode
from .store import fetch_spans


def _format_node(node: TraceNode) -> str:
    """One indented line per span: agent · step_type · name (cost, duration)."""

    span = node.span
    indent = "  " * node.depth
    branch = "+- " if node.depth else ""
    status = span.get("status", "ok")
    mark = "x" if status == "error" else "*"
    flag = "  [ORPHAN]" if node.orphaned else ""

    cost = float(span.get("cost_usd", 0.0) or 0.0)
    dur = span.get("duration_ms")
    dur_str = f"{float(dur):.0f}ms" if dur is not None else "..."

    # ASCII-only on purpose: this prints fine on a default Windows console
    # (cp1252) as well as a UTF-8 Linux/Docker terminal.
    return (
        f"{indent}{branch}{mark} {span.get('agent_name', '?')} | "
        f"{span.get('step_type', '?')} | {span.get('name', '')} "
        f"(${cost:.4f}, {dur_str}){flag}"
    )


def _print_tree(nodes: list[TraceNode]) -> None:
    for node in nodes:
        print(_format_node(node))
        _print_tree(node.children)


def _print_human(trace: AssembledTrace) -> None:
    s = trace.summary
    print(f"\nTrace {trace.trace_id} -- {trace.span_count} spans")
    print("=" * 60)
    _print_tree(trace.roots)
    print("=" * 60)
    print(
        f"cost ${s.total_cost_usd:.4f} | {s.total_tokens} tokens "
        f"({s.total_tokens_in} in / {s.total_tokens_out} out)"
    )
    print(
        f"wall-clock {s.wall_clock_ms:.0f}ms | compute {s.sum_step_ms:.0f}ms | "
        f"{s.step_count} steps | {s.agent_count} agents | {s.error_count} errors"
    )
    if trace.orphaned_span_ids:
        print(f"orphaned spans (missing parent): {trace.orphaned_span_ids}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m backend.correlation")
    parser.add_argument("trace_id", help="the trace_id to assemble and print")
    parser.add_argument("--json", action="store_true", help="emit JSON, not a tree")
    args = parser.parse_args(argv)

    # Import here so `--help` works even without the ClickHouse driver installed.
    from backend.storage.clickhouse import get_client

    client = get_client()
    spans = fetch_spans(client, args.trace_id)
    if not spans:
        print(f"No spans found for trace_id {args.trace_id!r}.", file=sys.stderr)
        return 1

    trace = build_trace(spans, args.trace_id)
    if args.json:
        print(json.dumps(trace.to_dict(), indent=2, default=str))
    else:
        _print_human(trace)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
