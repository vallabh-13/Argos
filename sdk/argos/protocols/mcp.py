"""MCP (Model Context Protocol) tool-call adapter.

MCP is how an agent reaches an external tool/resource. When an agent calls a tool
over MCP, Argos records it as a ``tool_call`` span carrying the standard ``mcp.*``
attributes (which server, which tool, which transport) so the trace shows exactly
what was invoked — and, when a tool keeps failing, the repeated-tool-failure rule
has a stable signature to catch.

Like the A2A adapter, it keeps adoption to ~2 lines:

    from argos.protocols import mcp_tool_call

    with mcp_tool_call(agent_name="search", server="search-tools", tool="web_search") as step:
        result = mcp_client.call_tool("web_search", {"query": q})
        step.set_attribute("results_count", len(result))

The span ``name`` defaults to ``"{server}.{tool}"`` and is stable across calls,
so a retry storm against the same tool produces one repeated signature — exactly
what the loop / repeated-failure detectors key on.

This is an **in-process** adapter: it models the MCP call shape and records it
faithfully, but does not stand up a real MCP server/transport. The product is the
tracing; the demo tool only needs to produce genuine tool_call spans (including
genuine *failures* when it returns garbage).
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator, Optional

from ..tracing import StepRecorder, trace_step


@contextmanager
def mcp_tool_call(
    *,
    agent_name: str,
    server: str,
    tool: str,
    transport: str = "stdio",
    name: Optional[str] = None,
    attributes: Optional[dict[str, Any]] = None,
) -> Iterator[StepRecorder]:
    """Trace one MCP tool call. Use as a context manager.

    ``agent_name`` is the agent making the call; ``server`` / ``tool`` identify
    what's being invoked and ``transport`` records how (``"stdio"`` by default).
    All three are recorded as ``mcp.*`` attributes. Yields the
    :class:`StepRecorder` so the caller can attach results or, if the tool fails,
    call ``step.set_error(...)`` to mark the span as an error.
    """

    attrs: dict[str, Any] = dict(attributes or {})
    attrs["mcp.server"] = server
    attrs["mcp.tool"] = tool
    attrs["mcp.transport"] = transport

    label = name or f"{server}.{tool}"

    with trace_step(
        agent_name=agent_name,
        step_type="tool_call",
        name=label,
        attributes=attrs,
    ) as step:
        yield step
