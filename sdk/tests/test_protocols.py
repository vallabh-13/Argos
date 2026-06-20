"""Tests for the protocol adapters (sdk/argos/protocols/).

These prove the two thin adapters do their one job: emit a span of the right
``step_type`` with the standardized ``a2a.*`` / ``mcp.*`` attributes, while still
behaving like a normal ``trace_step`` (real ids, nesting, error capture). The
nesting test is the important one — it's what gives the multi-agent tree its
orchestrator → handoff → tool shape.
"""

import pytest

from argos import init_tracing
from argos.protocols import a2a_handoff, mcp_tool_call
from argos.span import Span, Status, StepType


@pytest.fixture
def captured():
    """Install a sink that captures emitted spans instead of printing them."""

    spans: list[Span] = []
    init_tracing(service="test-service", sink=spans.append)
    return spans


def test_a2a_handoff_emits_handoff_span_with_attributes(captured):
    with a2a_handoff(
        from_agent="orchestrator", to_agent="search", task="find sources", task_id="t-1"
    ):
        pass

    assert len(captured) == 1
    span = captured[0]
    assert span.step_type is StepType.A2A_HANDOFF
    assert span.agent_name == "orchestrator"  # the initiating agent owns the span
    assert span.attributes["a2a.from"] == "orchestrator"
    assert span.attributes["a2a.to"] == "search"
    assert span.attributes["a2a.task"] == "find sources"
    assert span.attributes["a2a.task_id"] == "t-1"
    assert span.trace_id and span.span_id


def test_mcp_tool_call_emits_tool_span_with_attributes(captured):
    with mcp_tool_call(
        agent_name="search", server="search-tools", tool="web_search"
    ) as step:
        step.set_attribute("results_count", 5)

    span = captured[0]
    assert span.step_type is StepType.TOOL_CALL
    assert span.agent_name == "search"
    assert span.name == "search-tools.web_search"  # stable signature for detection
    assert span.attributes["mcp.server"] == "search-tools"
    assert span.attributes["mcp.tool"] == "web_search"
    assert span.attributes["mcp.transport"] == "stdio"
    assert span.attributes["results_count"] == 5


def test_mcp_tool_call_records_error(captured):
    with mcp_tool_call(agent_name="search", server="search-tools", tool="web_search") as step:
        step.set_error("malformed upstream response")

    span = captured[0]
    assert span.status is Status.ERROR
    assert span.error_message == "malformed upstream response"


def test_handoff_nests_tool_call_as_child(captured):
    # orchestrator hands off to search, which then calls a tool: the tool span
    # should nest beneath the handoff span (OTel context propagation).
    with a2a_handoff(from_agent="orchestrator", to_agent="search"):
        with mcp_tool_call(agent_name="search", server="search-tools", tool="web_search"):
            pass

    inner, outer = captured[0], captured[1]
    assert inner.step_type is StepType.TOOL_CALL
    assert outer.step_type is StepType.A2A_HANDOFF
    assert inner.trace_id == outer.trace_id
    assert inner.parent_span_id == outer.span_id
    assert outer.parent_span_id is None
