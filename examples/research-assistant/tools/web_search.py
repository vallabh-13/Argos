"""An in-process stand-in for a web-search tool the search agent calls over MCP.

The point of the demo is to generate *genuine* spans, not to search the real web,
so this returns canned-but-plausible results. It has two modes:

* normal (``fail=False``) — a well-formed result set the agent can use.
* broken (``fail=True``)  — a genuinely malformed payload: the ``results`` field
  is corrupt text instead of a list of result objects. This is NOT a fabricated
  error span — the tool really hands back garbage, the agent's own validation
  really rejects it, and that drives the real retry loop in the failure scenario.
"""

from __future__ import annotations

from typing import Any


def web_search(query: str, *, fail: bool = False) -> dict[str, Any]:
    """Return search results for ``query`` (or garbage when ``fail`` is set)."""

    if fail:
        # A malformed upstream response: claims success but the payload is corrupt
        # (results should be a list of objects; here it's broken bytes-ish text).
        return {"status": "ok", "results": "�� CORRUPT \x00 RESPONSE ��"}

    results = [
        {
            "title": f"{query} — overview",
            "url": "https://example.com/overview",
            "snippet": f"Background and key facts about {query}.",
        },
        {
            "title": f"{query} — recent developments",
            "url": "https://example.com/recent",
            "snippet": f"What's changed lately regarding {query}.",
        },
        {
            "title": f"{query} — analysis",
            "url": "https://example.com/analysis",
            "snippet": f"Implications and expert analysis of {query}.",
        },
    ]
    return {"status": "ok", "results": results}
