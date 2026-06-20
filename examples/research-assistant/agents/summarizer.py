"""The summarizer agent: turn the search findings into a final answer."""

from __future__ import annotations

from argos import trace_step

from pricing import cost_usd


def summarize(llm, question: str, findings: dict) -> str:
    """Write the final answer from the findings (one real LLM call)."""

    with trace_step(agent_name="summarizer", step_type="llm_call", name="summarize") as step:
        step.set_attribute("question", question)

        if findings.get("failed"):
            # Nothing usable to summarize. Record the error and skip the LLM call
            # (no point spending tokens to say "I can't"); return a degraded answer.
            step.set_error("no usable search results to summarize")
            return "No answer — the search tool failed repeatedly and the run was aborted."

        system = "You are a concise research assistant. Answer the question using the findings."
        content = f"Question: {question}\n\nFindings:\n{findings.get('extract', '')}"
        res = llm.converse(system=system, messages=[{"role": "user", "content": content}])
        step.set_usage(model=res.model, tokens_in=res.tokens_in, tokens_out=res.tokens_out)
        step.set_cost(cost_usd(res.model, res.tokens_in, res.tokens_out))
        return res.text
