"""Per-token cost for the demo's Bedrock model.

The SDK records ``cost_usd`` per span; this module turns the *real* token counts
the Bedrock Converse API returns into that dollar figure. Keeping it tiny and
pure means the cost shown in Argos is the actual cost of the call, not a guess.

Rates are USD per 1,000 tokens (Claude 3 Haiku, on-demand). Update here if AWS
changes pricing.
"""

from __future__ import annotations

from typing import Optional

# (input_per_1k, output_per_1k) in USD.
_HAIKU = (0.00025, 0.00125)

_PER_1K: dict[str, tuple[float, float]] = {
    "anthropic.claude-3-haiku-20240307-v1:0": _HAIKU,
}

# Cross-region inference-profile ids carry a region prefix (e.g. "us.anthropic...").
# Strip it so the same rate table matches either form.
_PROFILE_PREFIXES = ("us.", "eu.", "apac.")


def _rates(model: Optional[str]) -> tuple[float, float]:
    if not model:
        return _HAIKU
    base = model
    for prefix in _PROFILE_PREFIXES:
        if base.startswith(prefix):
            base = base[len(prefix):]
            break
    if base in _PER_1K:
        return _PER_1K[base]
    # The demo only ever uses Haiku; fall back to its rates rather than 0 so an
    # unrecognized id still produces a non-zero (and roughly right) cost.
    return _HAIKU


def cost_usd(model: Optional[str], tokens_in: int, tokens_out: int) -> float:
    """Dollar cost of one LLM call from its model id and token counts."""

    cin, cout = _rates(model)
    return round((tokens_in / 1000.0) * cin + (tokens_out / 1000.0) * cout, 8)
