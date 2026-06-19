"""Argos SDK — OpenTelemetry-native tracing for multi-agent AI systems.

Public API (everything most users need):

    from argos import init_tracing, trace_step

    init_tracing(service="my-app")
    with trace_step(agent_name="search", step_type="tool_call", name="web.search") as step:
        step.set_usage(model="...", tokens_in=120, tokens_out=80)
        step.set_cost(0.011)

The ``Span`` / ``StepType`` data model is exported too for advanced use, tests,
and the backend pipeline.
"""

from .span import Span, StepType, Status
from .tracing import init_tracing, trace_step, StepRecorder

__all__ = [
    "init_tracing",
    "trace_step",
    "StepRecorder",
    "Span",
    "StepType",
    "Status",
]

__version__ = "0.1.0"
