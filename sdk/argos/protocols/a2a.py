"""A2A (agent-to-agent) handoff adapter.

When one agent hands a task to another, that handoff is itself a step worth
recording — it's the edge in the multi-agent graph that single-agent tracers
miss entirely. This adapter is a thin wrapper over :func:`argos.trace_step` that
emits an ``a2a_handoff`` span and stamps the standard ``a2a.*`` attributes so the
correlation engine (and the Grafana trace-detail panel) can show *who handed what
to whom*.

It stays true to the SDK's adoption promise — instrumenting a handoff is still
~2 lines:

    from argos.protocols import a2a_handoff

    with a2a_handoff(from_agent="orchestrator", to_agent="search", task="find sources"):
        result = search_agent.run(...)

The handoff span is attributed to ``from_agent`` (the agent *initiating* the
handoff). Because the body runs *inside* the ``with`` block, the receiving
agent's own spans automatically nest beneath the handoff — giving the tree its
orchestrator → handoff → search shape for free via OTel context propagation.

This is an **in-process** adapter: it models the A2A protocol's shape (typed
message: from / to / task / task_id) and records it faithfully, but the call does
not cross a network. That's a deliberate simplicity choice — Argos's product is
the tracing, and the demo agents only need to *generate* genuine spans.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator, Optional

from ..tracing import StepRecorder, trace_step


@contextmanager
def a2a_handoff(
    *,
    from_agent: str,
    to_agent: str,
    task: Optional[str] = None,
    task_id: Optional[str] = None,
    name: Optional[str] = None,
    attributes: Optional[dict[str, Any]] = None,
) -> Iterator[StepRecorder]:
    """Trace one A2A handoff. Use as a context manager.

    ``from_agent`` / ``to_agent`` are the two ends of the handoff; ``task`` is a
    human-readable description of what's being delegated, and ``task_id`` (if the
    caller tracks one) ties the handoff to a unit of work. All three are recorded
    as ``a2a.*`` attributes. Yields the :class:`StepRecorder` so the caller can
    attach extra metadata or mark the handoff as failed.
    """

    attrs: dict[str, Any] = dict(attributes or {})
    attrs["a2a.from"] = from_agent
    attrs["a2a.to"] = to_agent
    if task is not None:
        attrs["a2a.task"] = task
    if task_id is not None:
        attrs["a2a.task_id"] = task_id

    # A stable, descriptive label. It's also the span ``name``, which the loop
    # detector keys on — so a ping-ponging handoff (A→B repeated) has a constant
    # signature and trips the runaway-loop rule.
    label = name or f"a2a: {from_agent} -> {to_agent}"

    with trace_step(
        agent_name=from_agent,
        step_type="a2a_handoff",
        name=label,
        attributes=attrs,
    ) as step:
        yield step
