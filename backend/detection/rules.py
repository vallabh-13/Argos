"""The three detection rules — pure functions over an AssembledTrace.

Each rule has the same shape::

    detect_xxx(trace: AssembledTrace, config: DetectionConfig) -> list[Finding]

No I/O, no Prometheus — so each is unit-tested on a hand-built trace with no
infrastructure. ``engine.run_detection`` just calls all three.

All three start from the same flat view of the trace's spans (``_iter_spans``),
which walks the causal tree cycle-safely: a malformed loop can't make detection
itself hang.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Iterator

from ..correlation.models import AssembledTrace, TraceNode
from .models import DetectionConfig, Finding, Severity


def _iter_spans(trace: AssembledTrace) -> Iterator[dict[str, Any]]:
    """Yield every span dict in the trace once (cycle-safe DFS over the tree)."""

    seen: set[str] = set()
    stack: list[TraceNode] = list(trace.roots)
    while stack:
        node = stack.pop()
        sid = node.span_id
        if sid in seen:
            continue
        if sid is not None:
            seen.add(sid)
        yield node.span
        stack.extend(node.children)


# --------------------------------------------------------------------------
# Rule 1 — runaway loop
# --------------------------------------------------------------------------
def detect_runaway_loops(
    trace: AssembledTrace, config: DetectionConfig
) -> list[Finding]:
    """Flag a step that repeats too many times — a retry-storm or handoff ping-pong.

    The "loop signature" is ``(agent_name, step_type, name)``: the same agent
    doing the same labelled step. If one signature appears ``loop_count`` times
    or more, that's a runaway loop.
    """

    groups: dict[tuple, list[str]] = defaultdict(list)
    for span in _iter_spans(trace):
        signature = (span.get("agent_name"), span.get("step_type"), span.get("name"))
        groups[signature].append(span.get("span_id"))

    findings: list[Finding] = []
    for (agent, step_type, name), span_ids in groups.items():
        count = len(span_ids)
        if count < config.loop_count:
            continue
        # Way past the threshold (2x) is loud enough to page on.
        severity = Severity.CRITICAL if count >= 2 * config.loop_count else Severity.WARNING
        findings.append(
            Finding(
                rule="runaway_loop",
                severity=severity,
                trace_id=trace.trace_id,
                summary=(
                    f"{agent} repeated '{name}' ({step_type}) {count}x "
                    f"(threshold {config.loop_count})"
                ),
                span_ids=span_ids,
                evidence={
                    "agent_name": agent,
                    "step_type": step_type,
                    "name": name,
                    "count": count,
                    "threshold": config.loop_count,
                },
            )
        )
    return findings


# --------------------------------------------------------------------------
# Rule 2 — repeated tool failure
# --------------------------------------------------------------------------
def detect_repeated_tool_failures(
    trace: AssembledTrace, config: DetectionConfig
) -> list[Finding]:
    """Flag a single tool that keeps erroring — a failing dependency, not a fluke.

    Looks only at ``tool_call`` spans with ``status == "error"``, grouped by
    ``(agent_name, name)``. A one-off error is noise; the same tool failing
    ``failure_count`` times or more is the signal.
    """

    groups: dict[tuple, dict[str, list]] = defaultdict(
        lambda: {"span_ids": [], "errors": []}
    )
    for span in _iter_spans(trace):
        if span.get("step_type") != "tool_call" or span.get("status") != "error":
            continue
        key = (span.get("agent_name"), span.get("name"))
        groups[key]["span_ids"].append(span.get("span_id"))
        groups[key]["errors"].append(span.get("error_message"))

    findings: list[Finding] = []
    for (agent, name), data in groups.items():
        count = len(data["span_ids"])
        if count < config.failure_count:
            continue
        severity = (
            Severity.CRITICAL if count >= 2 * config.failure_count else Severity.WARNING
        )
        findings.append(
            Finding(
                rule="repeated_tool_failure",
                severity=severity,
                trace_id=trace.trace_id,
                summary=(
                    f"{agent}'s tool '{name}' failed {count}x "
                    f"(threshold {config.failure_count})"
                ),
                span_ids=data["span_ids"],
                evidence={
                    "agent_name": agent,
                    "tool": name,
                    "count": count,
                    "threshold": config.failure_count,
                    "error_messages": data["errors"],
                },
            )
        )
    return findings


# --------------------------------------------------------------------------
# Rule 3 — cost spike
# --------------------------------------------------------------------------
def detect_cost_spikes(
    trace: AssembledTrace, config: DetectionConfig
) -> list[Finding]:
    """Flag a run whose total cost crossed the budget.

    Uses the per-run total the correlation engine already computed. Fires when
    cost is **strictly greater** than the limit (a run exactly at budget is OK).
    """

    total = trace.summary.total_cost_usd if trace.summary else 0.0
    if total <= config.cost_limit_usd:
        return []

    # Surface the priciest spans as evidence — the place to look first.
    spans = sorted(
        _iter_spans(trace),
        key=lambda s: float(s.get("cost_usd", 0.0) or 0.0),
        reverse=True,
    )
    top = [
        {"span_id": s.get("span_id"), "cost_usd": float(s.get("cost_usd", 0.0) or 0.0)}
        for s in spans[:3]
        if float(s.get("cost_usd", 0.0) or 0.0) > 0
    ]

    severity = (
        Severity.CRITICAL if total > 2 * config.cost_limit_usd else Severity.WARNING
    )
    return [
        Finding(
            rule="cost_spike",
            severity=severity,
            trace_id=trace.trace_id,
            summary=(
                f"run cost ${total:.4f} exceeded limit ${config.cost_limit_usd:.4f}"
            ),
            span_ids=[t["span_id"] for t in top],
            evidence={
                "total_cost_usd": total,
                "limit_usd": config.cost_limit_usd,
                "top_spans": top,
            },
        )
    ]
