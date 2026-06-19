"""Secret redaction — blank out sensitive values *before* a span leaves the box.

This is Argos's headline security rule (README §8): API keys, tokens, and
passwords must never be transmitted or stored. The SDK calls into this module
right before emitting a span, so redaction happens on the user's own machine.

Two complementary strategies:

1. **Key-name denylist** — if an attribute is *named* like a secret
   (``api_key``, ``password``, ``authorization`` ...), blank its value
   regardless of what the value looks like.
2. **Value patterns** — if a value *looks* like a known secret shape
   (``sk-...`` OpenAI keys, ``AKIA...`` AWS keys, ``Bearer ...`` headers), blank
   it even if the key name was innocent (e.g. a secret pasted into ``"note"``).

Redacted values become the constant ``REDACTION_PLACEHOLDER`` ("[REDACTED]") —
a full blank that leaks zero characters.

This module has no third-party dependencies on purpose: security code should be
small, obvious, and trivial to audit.
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Optional

REDACTION_PLACEHOLDER = "[REDACTED]"

# Attribute names whose *value* is always sensitive. Matching is done after
# stripping separators and lowercasing (see _normalize), so "api_key",
# "OpenAI-API-Key", and "user_password" all hit despite different punctuation.
#
# Bias note: this is a deliberately safe-by-default substring match — a field
# named "token_count" will be blanked too. We'd rather over-redact a stray value
# than leak a real secret; the span model has dedicated tokens_in/tokens_out
# fields for real counts, so this rarely bites in practice.
DEFAULT_DENYLIST_KEYS: frozenset[str] = frozenset(
    {
        "password",
        "passwd",
        "secret",
        "token",
        "api_key",
        "access_key",
        "secret_key",
        "authorization",
        "credential",
        "private_key",
        "session_id",
    }
)

# Value shapes that are secrets no matter where they appear. Kept small and
# specific so we don't accidentally blank ordinary text.
DEFAULT_VALUE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"sk-[A-Za-z0-9_\-]{16,}"),          # OpenAI-style secret keys
    re.compile(r"AKIA[0-9A-Z]{16}"),                 # AWS access key IDs
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-]{8,}"), # Bearer auth headers
    re.compile(r"AIza[0-9A-Za-z_\-]{20,}"),          # Google API keys
    re.compile(r"ghp_[A-Za-z0-9]{20,}"),             # GitHub personal tokens
    re.compile(r"xox[baprs]-[A-Za-z0-9\-]{10,}"),    # Slack tokens
)


def _normalize(name: str) -> str:
    """Lowercase and strip non-alphanumerics so punctuation can't dodge a match.

    "OpenAI-API-Key" and "api_key" both normalize to contain "apikey", so the
    denylist matches regardless of hyphen / underscore / casing differences.
    """

    return re.sub(r"[^a-z0-9]", "", name.lower())


def _key_is_sensitive(key: str, denylist: Iterable[str]) -> bool:
    """True if the attribute *name* matches the denylist (separator-insensitive)."""

    normalized_key = _normalize(key)
    return any(_normalize(banned) in normalized_key for banned in denylist)


def _value_looks_sensitive(
    value: str, patterns: Iterable[re.Pattern[str]]
) -> bool:
    """True if the *value* matches any known secret pattern."""

    return any(p.search(value) for p in patterns)


def redact_value(
    value: Any,
    *,
    patterns: Optional[Iterable[re.Pattern[str]]] = None,
) -> Any:
    """Blank a single value if it *looks* like a secret; otherwise pass through.

    Non-strings (ints, floats, bools) are returned unchanged — a token count or
    a cost is never a secret, and blanking it would corrupt the data.
    """

    if not isinstance(value, str):
        return value
    pats = DEFAULT_VALUE_PATTERNS if patterns is None else tuple(patterns)
    if _value_looks_sensitive(value, pats):
        return REDACTION_PLACEHOLDER
    return value


def redact_mapping(
    attributes: dict[str, Any],
    *,
    extra_denylist_keys: Optional[Iterable[str]] = None,
    extra_patterns: Optional[Iterable[re.Pattern[str]]] = None,
) -> dict[str, Any]:
    """Return a redacted copy of an attribute mapping.

    Applies both strategies: a value is blanked if its *key* is denylisted OR
    its *value* matches a secret pattern. Recurses into nested dicts and into
    lists so secrets can't hide one level down.

    The input is never mutated — we return a new dict so the caller's original
    data is untouched.
    """

    denylist = set(DEFAULT_DENYLIST_KEYS)
    if extra_denylist_keys:
        denylist.update(k.lower() for k in extra_denylist_keys)

    patterns = DEFAULT_VALUE_PATTERNS
    if extra_patterns:
        patterns = DEFAULT_VALUE_PATTERNS + tuple(extra_patterns)

    result: dict[str, Any] = {}
    for key, value in attributes.items():
        if _key_is_sensitive(key, denylist):
            result[key] = REDACTION_PLACEHOLDER
        elif isinstance(value, dict):
            result[key] = redact_mapping(
                value,
                extra_denylist_keys=extra_denylist_keys,
                extra_patterns=extra_patterns,
            )
        elif isinstance(value, list):
            result[key] = [
                redact_mapping(
                    item,
                    extra_denylist_keys=extra_denylist_keys,
                    extra_patterns=extra_patterns,
                )
                if isinstance(item, dict)
                else redact_value(item, patterns=patterns)
                for item in value
            ]
        else:
            result[key] = redact_value(value, patterns=patterns)
    return result
