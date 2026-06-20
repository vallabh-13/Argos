"""The search agent: call the web-search tool over MCP, then extract key facts.

This is where the demo's real failure mode lives. The agent calls the tool,
*validates the result itself*, and retries if the result is unusable. In the
normal scenario the first call validates and we move on. In the failure scenario
the tool keeps returning garbage, so the agent keeps retrying until it hits the
bounded cap — producing a genuine runaway loop of failing ``tool_call`` spans
that the Phase 4 detectors catch. No spans are faked; the loop is real agent
behavior reacting to a real bad tool.
"""

from __future__ import annotations

import json
import os
from typing import Any

from argos import mcp_tool_call, trace_step

from pricing import cost_usd
from tools.web_search import web_search


def _looks_valid(payload: Any) -> bool:
    """Is the tool's response a usable result set?

    A well-formed response is a dict whose ``results`` is a non-empty list of
    objects that each carry a ``snippet``. The garbage payload (a string) fails
    this — which is exactly what makes the agent retry.
    """

    return (
        isinstance(payload, dict)
        and isinstance(payload.get("results"), list)
        and len(payload["results"]) > 0
        and all(isinstance(r, dict) and "snippet" in r for r in payload["results"])
    )


def gather(llm, query: str, *, scenario: str = "happy", max_retries: int | None = None) -> dict:
    """Run the tool (with retries) and extract facts. Returns a findings dict."""

    if max_retries is None:
        max_retries = int(os.getenv("ARGOS_DEMO_MAX_RETRIES", "6"))
    fail = scenario == "fail"

    results = None
    for attempt in range(1, max_retries + 1):
        # Each tool call is one MCP span. A stable name ("search-tools.web_search")
        # means repeated failures share a signature the detectors can group on.
        with mcp_tool_call(
            agent_name="search", server="search-tools", tool="web_search"
        ) as step:
            step.set_attribute("query", query)
            step.set_attribute("attempt", attempt)
            # A planted secret to prove redaction still runs on real spans
            # (security-first): this never reaches storage un-blanked.
            step.set_attribute("upstream_auth", "Bearer sk-demo-abcdef0123456789ghijkl")

            payload = web_search(query, fail=fail)

            if _looks_valid(payload):
                results = payload["results"]
                step.set_attribute("results_count", len(results))
                break

            # Genuinely unusable result -> mark this tool_call as an error and loop.
            step.set_error(f"malformed tool response (attempt {attempt})")
            step.set_attribute("raw_preview", str(payload)[:120])

    if results is None:
        # Retries exhausted: report failure upward. The summarizer will degrade.
        return {"query": query, "results": [], "extract": "", "failed": True,
                "attempts": max_retries}

    # Extract the key facts — a real LLM call (cheap; capped output).
    with trace_step(agent_name="search", step_type="llm_call", name="extract") as step:
        system = "Extract the 3 most relevant facts from these search results as short bullets."
        content = json.dumps(results)
        res = llm.converse(system=system, messages=[{"role": "user", "content": content}])
        step.set_usage(model=res.model, tokens_in=res.tokens_in, tokens_out=res.tokens_out)
        step.set_cost(cost_usd(res.model, res.tokens_in, res.tokens_out))
        step.set_attribute("query", query)
        step.set_attribute("results_count", len(results))

    return {"query": query, "results": results, "extract": res.text, "failed": False}
