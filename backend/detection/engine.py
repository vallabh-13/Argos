"""Run every detection rule over a trace and collect the findings.

Thin on purpose: the intelligence lives in ``rules.py``. This just registers the
rule set and runs them in order, so adding a rule later is a one-line change here.
"""

from __future__ import annotations

from typing import Callable

from ..correlation.models import AssembledTrace
from .models import DetectionConfig, Finding
from .rules import (
    detect_cost_spikes,
    detect_repeated_tool_failures,
    detect_runaway_loops,
)

# The registered rules. Each is (trace, config) -> list[Finding].
RULES: list[Callable[[AssembledTrace, DetectionConfig], list[Finding]]] = [
    detect_runaway_loops,
    detect_repeated_tool_failures,
    detect_cost_spikes,
]


def run_detection(
    trace: AssembledTrace, config: DetectionConfig | None = None
) -> list[Finding]:
    """Run all rules over ``trace`` and return the combined findings.

    A clean trace returns an empty list. Defaults to the standard thresholds if
    no config is given.
    """

    config = config or DetectionConfig()
    findings: list[Finding] = []
    for rule in RULES:
        findings.extend(rule(trace, config))
    return findings
