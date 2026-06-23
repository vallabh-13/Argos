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
from .sinks import console_sink, KafkaSink
from .tracing import init_tracing, trace_step, StepRecorder
from .config import ArgosConfig, DetectionThresholds, load_config
from .protocols import a2a_handoff, mcp_tool_call

__all__ = [
    "init_tracing",
    "trace_step",
    "StepRecorder",
    "Span",
    "StepType",
    "Status",
    "console_sink",
    "KafkaSink",
    "ArgosConfig",
    "DetectionThresholds",
    "load_config",
    "a2a_handoff",
    "mcp_tool_call",
]

__version__ = "0.1.0"
