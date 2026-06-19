"""The Argos Span data model — the shared contract of the whole system.

A *span* is one recorded step an agent took: an LLM call, a tool call, an
agent-to-agent handoff, or a decision. Every span carries a ``trace_id`` so the
correlation engine (Phase 3) can later reassemble all the steps of one user
request into a single causal timeline.

This module deliberately has **no dependencies** (not even OpenTelemetry). The
SDK emits this model, the ingest pipeline (Phase 2) stores it, and the
correlation engine (Phase 3) stitches it — so keeping it small and pure makes
every other phase easier to build and test.

Field reference: docs/PROJECT.md §05 (Span + Storage Schema).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional


class StepType(str, Enum):
    """The kind of step a span records.

    Subclassing ``str`` means a ``StepType`` is also a plain string, so it
    serializes to JSON as ``"tool_call"`` rather than ``"StepType.TOOL_CALL"``
    while still giving us a closed set of valid values (a typo like
    ``"toolcall"`` raises instead of silently producing bad data).
    """

    LLM_CALL = "llm_call"
    TOOL_CALL = "tool_call"
    A2A_HANDOFF = "a2a_handoff"
    DECISION = "decision"


class Status(str, Enum):
    """Did the step succeed or fail?"""

    OK = "ok"
    ERROR = "error"


def _utc_now() -> datetime:
    """Timezone-aware UTC timestamp.

    We always store UTC so spans from agents in different timezones line up on
    one timeline. ClickHouse (Phase 2) expects this too.
    """

    return datetime.now(timezone.utc)


@dataclass
class Span:
    """One recorded agent step.

    Only ``service_name``, ``agent_name``, ``step_type`` and ``name`` are
    required to create a span; the IDs and timestamps default sensibly so the
    emitter (``tracing.py``) can fill them in. Cost/token/model fields are
    optional because not every step is an LLM call.
    """

    # --- identity & causal structure -------------------------------------
    service_name: str
    agent_name: str
    step_type: StepType
    name: str

    trace_id: str = ""           # shared across one user request; set by emitter
    span_id: str = ""            # unique per step; set by emitter
    parent_span_id: Optional[str] = None  # builds the causal tree (None = root)

    # --- timing ----------------------------------------------------------
    start_time: datetime = field(default_factory=_utc_now)
    end_time: Optional[datetime] = None  # set when the step finishes

    # --- outcome ---------------------------------------------------------
    status: Status = Status.OK
    error_message: Optional[str] = None

    # --- cost / usage (optional; mainly for llm_call steps) --------------
    model: Optional[str] = None
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0

    # --- extras & security ----------------------------------------------
    attributes: dict[str, Any] = field(default_factory=dict)  # MCP/A2A metadata
    redacted: bool = False       # proof that secrets were blanked before emit

    def __post_init__(self) -> None:
        # Accept plain strings (e.g. "tool_call") and coerce to the enum so
        # callers don't have to import StepType, but invalid values still fail
        # loudly here rather than downstream.
        if not isinstance(self.step_type, StepType):
            self.step_type = StepType(self.step_type)
        if not isinstance(self.status, Status):
            self.status = Status(self.status)

    @property
    def duration_ms(self) -> Optional[float]:
        """Milliseconds the step took, or ``None`` if it hasn't ended yet."""

        if self.end_time is None:
            return None
        return (self.end_time - self.start_time).total_seconds() * 1000.0

    def to_dict(self) -> dict[str, Any]:
        """Plain-dict form, JSON-friendly.

        Enums become their string values and datetimes become ISO-8601 strings,
        so the result drops straight into ``json.dumps`` or a Kafka message.
        """

        data = asdict(self)
        data["step_type"] = self.step_type.value
        data["status"] = self.status.value
        data["start_time"] = self.start_time.isoformat()
        data["end_time"] = self.end_time.isoformat() if self.end_time else None
        data["duration_ms"] = self.duration_ms
        return data

    def to_json(self, *, indent: Optional[int] = 2) -> str:
        """Pretty JSON string — what the demo prints to the console."""

        return json.dumps(self.to_dict(), indent=indent, default=str)
