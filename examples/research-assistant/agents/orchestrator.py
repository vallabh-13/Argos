"""The orchestrator agent: plan the work and delegate it over A2A.

It owns the root span for the whole run, decides a search query (one LLM call),
then hands off to the search agent and, in turn, to the summarizer — each handoff
recorded as an ``a2a_handoff`` span. Because each delegate runs *inside* the
handoff's ``with`` block, their spans nest beneath it automatically, giving the
trace its orchestrator → search → summarizer tree.
"""

from __future__ import annotations

from argos import a2a_handoff, trace_step

from agents.search import gather
from agents.summarizer import summarize
from pricing import cost_usd


def plan(llm, question: str) -> str:
    """Decide the web-search query for the question (one real LLM call)."""

    with trace_step(agent_name="orchestrator", step_type="llm_call", name="plan") as step:
        system = (
            "You are a research orchestrator. Given a question, reply with a single "
            "concise web-search query and nothing else."
        )
        res = llm.converse(system=system, messages=[{"role": "user", "content": question}])
        step.set_usage(model=res.model, tokens_in=res.tokens_in, tokens_out=res.tokens_out)
        step.set_cost(cost_usd(res.model, res.tokens_in, res.tokens_out))

        query = res.text.strip().splitlines()[0][:200] if res.text.strip() else question
        step.set_attribute("question", question)
        step.set_attribute("search_query", query)
        return query


def orchestrate(llm, question: str, scenario: str = "happy") -> str:
    """Run the whole multi-agent flow and return the final answer."""

    with trace_step(agent_name="orchestrator", step_type="decision", name="orchestrate") as root:
        root.set_attribute("question", question)
        root.set_attribute("scenario", scenario)

        query = plan(llm, question)

        with a2a_handoff(from_agent="orchestrator", to_agent="search", task="gather sources"):
            findings = gather(llm, query, scenario=scenario)

        with a2a_handoff(from_agent="search", to_agent="summarizer", task="summarize findings"):
            answer = summarize(llm, question, findings)

        with a2a_handoff(
            from_agent="summarizer", to_agent="orchestrator", task="return answer"
        ) as handback:
            if findings.get("failed"):
                handback.set_error("search failed; no answer to return")

        root.set_attribute("answer_preview", str(answer)[:160])
        return answer
