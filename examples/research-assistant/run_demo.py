"""Phase 1 demo — a tiny fake agent that emits one Argos span.

This is intentionally minimal. The full multi-agent research-assistant demo
(orchestrator → search → summarizer over A2A/MCP) grows here in later phases.
For now its only job is to prove the Phase 1 outcome from docs/PROJECT.md:

    "running an agent prints structured spans to the console"

...and to show that secret redaction happens *before* the span is emitted.

Run it:
    pip install -e sdk        # from the repo root, once
    python examples/research-assistant/run_demo.py
"""

from argos import init_tracing, trace_step


def fake_search_agent(query: str) -> str:
    """Pretend to be a search agent doing one tool call.

    We wrap the step in `trace_step` (3 lines) and attach realistic metadata —
    including two planted secrets — to demonstrate redaction.
    """

    with trace_step(
        agent_name="search",
        step_type="tool_call",
        name="web.search",
    ) as step:
        # Token/cost data — the stuff Argos uses for per-step cost (Phase 3).
        step.set_usage(model="anthropic.claude-3-haiku", tokens_in=128, tokens_out=64)
        step.set_cost(0.0011)

        # Ordinary, safe metadata — preserved as-is.
        step.set_attribute("query", query)
        step.set_attribute("tool", "web_search_v1")
        step.set_attribute("results_count", 5)

        # PLANTED SECRET #1 — caught by the key-name denylist ("api_key").
        step.set_attribute("api_key", "sk-supersecret-DO-NOT-LEAK-1234567890")

        # PLANTED SECRET #2 — caught by value pattern even though the field name
        # ("debug_note") looks innocent. A real key accidentally pasted in.
        step.set_attribute(
            "debug_note",
            "retry used token Bearer abcdef0123456789ghijkl",
        )

        # PLANTED SECRET #3 — nested one level down, to show recursion.
        step.set_attribute("auth", {"password": "hunter2", "user": "demo"})

        return f"(pretend results for: {query})"


def main() -> None:
    # The whole adoption story: one init line, then wrap steps.
    init_tracing(service="research-assistant")

    print("=== Argos Phase 1 demo: emitting one span ===\n")
    result = fake_search_agent("latest on fusion energy")
    print(f"\nAgent returned: {result}")
    print(
        "\nNotice above: api_key, the Bearer token in debug_note, and the nested "
        "password all show as [REDACTED] — scrubbed before the span was emitted."
    )


if __name__ == "__main__":
    main()
