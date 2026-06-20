"""Protocol-specific span adapters (MCP, A2A).

Thin context managers layered over :func:`argos.trace_step` that record the two
multi-agent interactions Argos exists to trace:

* :func:`a2a_handoff`  — one agent handing a task to another (an ``a2a_handoff`` span).
* :func:`mcp_tool_call` — an agent calling a tool over MCP (a ``tool_call`` span).

Both stamp standardized ``a2a.*`` / ``mcp.*`` attributes so the correlation engine
and dashboard can show who-handed-what-to-whom and which tool was invoked. See
docs/PROJECT.md.
"""

from .a2a import a2a_handoff
from .mcp import mcp_tool_call

__all__ = ["a2a_handoff", "mcp_tool_call"]
