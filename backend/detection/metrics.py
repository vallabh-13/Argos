"""Prometheus metrics for detection.

``prometheus_client`` is imported here and *only* here, so importing the rules
never drags in the dependency. The metric design follows one hard rule:

    **No trace_id (or other unbounded id) in labels.**

Every distinct label-value combination is a separate time series in Prometheus;
putting a trace_id there would create millions of series and melt the database.
So we label only by bounded dimensions (rule name, severity) and use a histogram
+ id-less gauges for the per-run numbers.

Metrics exposed:
  * argos_traces_evaluated_total        Counter
  * argos_findings_total{rule,severity} Counter   <- the alerting signal
  * argos_run_cost_usd                  Histogram  (distribution of run costs)
  * argos_last_run_cost_usd             Gauge
  * argos_last_run_loops                Gauge      (max loop repetition seen)
  * argos_last_run_tool_failures        Gauge      (max single-tool failures seen)
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from prometheus_client import Counter, Gauge, Histogram, start_http_server

if TYPE_CHECKING:
    from ..correlation.models import AssembledTrace
    from .models import Finding

TRACES_EVALUATED = Counter(
    "argos_traces_evaluated_total", "Traces run through detection"
)
FINDINGS = Counter(
    "argos_findings_total", "Detection findings", ["rule", "severity"]
)
# Buckets sized for typical multi-agent run costs (cents to a few dollars).
RUN_COST = Histogram(
    "argos_run_cost_usd",
    "Per-run total cost (USD)",
    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0),
)
LAST_RUN_COST = Gauge("argos_last_run_cost_usd", "Cost of the most recent run (USD)")
LAST_RUN_LOOPS = Gauge(
    "argos_last_run_loops", "Max loop-signature repetition in the most recent run"
)
LAST_RUN_TOOL_FAILURES = Gauge(
    "argos_last_run_tool_failures", "Max single-tool failures in the most recent run"
)


def record(trace: "AssembledTrace", findings: "list[Finding]") -> None:
    """Update all metrics from one evaluated trace and its findings."""

    TRACES_EVALUATED.inc()

    cost = trace.summary.total_cost_usd if trace.summary else 0.0
    RUN_COST.observe(cost)
    LAST_RUN_COST.set(cost)

    for finding in findings:
        FINDINGS.labels(rule=finding.rule, severity=finding.severity.value).inc()

    # Reflect "how bad was the worst loop / tool failure this run" as gauges, read
    # from finding evidence. Reset to 0 when there were none, so the gauge tracks
    # the latest run rather than getting stuck high.
    loops = [f.evidence.get("count", 0) for f in findings if f.rule == "runaway_loop"]
    fails = [
        f.evidence.get("count", 0)
        for f in findings
        if f.rule == "repeated_tool_failure"
    ]
    LAST_RUN_LOOPS.set(max(loops) if loops else 0)
    LAST_RUN_TOOL_FAILURES.set(max(fails) if fails else 0)


def serve(port: int = 9108) -> None:
    """Start the /metrics HTTP endpoint Prometheus scrapes (non-blocking).

    ``start_http_server`` spawns a background thread, so the caller keeps control
    and must keep the process alive for Prometheus to scrape.
    """

    start_http_server(port)
