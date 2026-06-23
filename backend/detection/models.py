"""Detection data structures — findings, severity, and tunable thresholds.

Pure dataclasses, no I/O. The rules (``rules.py``) consume a
:class:`DetectionConfig` and emit :class:`Finding` objects; the Prometheus
exporter and the CLI read those. Keeping thresholds in one config object (with
sensible defaults and an ``from_env`` reader) is what keeps magic numbers out of
the rule code.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Severity(str, Enum):
    """How loud a finding is. Subclasses ``str`` so it serializes as "warning".

    Two levels keep alerting simple: WARNING = worth a look, CRITICAL = page
    someone. Rules escalate to CRITICAL when a threshold is badly exceeded.
    """

    WARNING = "warning"
    CRITICAL = "critical"


@dataclass
class Finding:
    """One thing a rule flagged about a trace.

    ``evidence`` is a free-form dict so each rule can attach the specifics that
    explain *why* it tripped (counts, the signature, the threshold it crossed)
    without forcing one rigid schema across very different rules.
    """

    rule: str                 # "runaway_loop" | "repeated_tool_failure" | "cost_spike"
    severity: Severity
    trace_id: str
    summary: str              # human-readable "what tripped"
    span_ids: list[str] = field(default_factory=list)  # the evidence spans
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule": self.rule,
            "severity": self.severity.value,
            "trace_id": self.trace_id,
            "summary": self.summary,
            "span_ids": self.span_ids,
            "evidence": self.evidence,
        }


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    return int(raw) if raw not in (None, "") else default


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    return float(raw) if raw not in (None, "") else default


@dataclass
class DetectionConfig:
    """Thresholds for the rules. One place; sensible defaults; no inline magic.

    * ``loop_count``     — a step signature repeating this many times (>=) is a loop.
    * ``failure_count``  — a tool failing this many times (>=) is a failure storm.
    * ``cost_limit_usd`` — a run costing strictly MORE than this (>) is a spike.
    """

    loop_count: int = 5
    failure_count: int = 3
    cost_limit_usd: float = 1.00

    @classmethod
    def from_env(cls, base: "DetectionConfig | None" = None) -> "DetectionConfig":
        """Read thresholds from env so docker/k8s can tune without code edits.

        ``base`` supplies the fallback values (its own defaults if omitted), so
        this can layer env overrides on top of config-file values.
        """

        base = base or cls()
        return cls(
            loop_count=_env_int("ARGOS_LOOP_COUNT", base.loop_count),
            failure_count=_env_int("ARGOS_FAILURE_COUNT", base.failure_count),
            cost_limit_usd=_env_float("ARGOS_COST_LIMIT_USD", base.cost_limit_usd),
        )

    @classmethod
    def from_config(cls) -> "DetectionConfig":
        """Resolve thresholds from the single config file, env still overriding.

        Precedence (lowest → highest): built-in defaults → ``argos.config.yml``
        → ``ARGOS_*`` env vars. This makes ``argos.config.yml`` the one place to
        tune thresholds while keeping env overrides for k8s/docker. Falls back to
        env-only if the SDK (which owns the config reader) isn't importable.
        """

        try:
            from argos.config import load_config
        except ImportError:
            return cls.from_env()

        det = load_config().detection
        file_base = cls(
            loop_count=det.loop_count,
            failure_count=det.failure_count,
            cost_limit_usd=det.cost_limit_usd,
        )
        return cls.from_env(base=file_base)
