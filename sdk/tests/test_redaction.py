"""Tests for secret redaction (sdk/argos/redaction.py).

Redaction is Argos's headline security rule, so these tests are deliberately
thorough: secrets must be blanked, and ordinary data must survive untouched
(over-redaction that corrupts real metrics is also a bug).
"""

import re

from argos.redaction import (
    REDACTION_PLACEHOLDER,
    redact_mapping,
    redact_value,
)


# --- value-pattern detection ---------------------------------------------
def test_openai_style_key_is_redacted():
    assert redact_value("sk-abcdef0123456789ABCDEF") == REDACTION_PLACEHOLDER


def test_aws_access_key_is_redacted():
    assert redact_value("AKIAIOSFODNN7EXAMPLE") == REDACTION_PLACEHOLDER


def test_bearer_token_is_redacted():
    assert redact_value("Bearer abcdef0123456789") == REDACTION_PLACEHOLDER


def test_ordinary_string_is_preserved():
    assert redact_value("latest on fusion energy") == "latest on fusion energy"


def test_non_strings_pass_through_unchanged():
    # Token counts and costs must never be blanked.
    assert redact_value(128) == 128
    assert redact_value(0.0011) == 0.0011
    assert redact_value(True) is True


# --- key-name denylist ----------------------------------------------------
def test_denylisted_key_is_redacted_regardless_of_value():
    out = redact_mapping({"api_key": "anything-at-all"})
    assert out["api_key"] == REDACTION_PLACEHOLDER


def test_denylist_is_case_insensitive_and_substring():
    out = redact_mapping({"OpenAI-API-Key": "x", "user_password": "y"})
    assert out["OpenAI-API-Key"] == REDACTION_PLACEHOLDER
    assert out["user_password"] == REDACTION_PLACEHOLDER


def test_innocent_key_with_secret_value_still_caught():
    # A real key pasted into a harmless-looking field is caught by pattern.
    out = redact_mapping({"note": "token is sk-abcdef0123456789ABCDEF"})
    assert out["note"] == REDACTION_PLACEHOLDER


# --- structure: nesting, lists, non-mutation ------------------------------
def test_nested_dict_is_redacted():
    out = redact_mapping({"auth": {"password": "hunter2", "user": "demo"}})
    assert out["auth"]["password"] == REDACTION_PLACEHOLDER
    assert out["auth"]["user"] == "demo"          # ordinary value survives


def test_secrets_inside_lists_are_redacted():
    out = redact_mapping({"keys": ["sk-abcdef0123456789ABCDEF", "ok-value"]})
    assert out["keys"][0] == REDACTION_PLACEHOLDER
    assert out["keys"][1] == "ok-value"


def test_input_is_not_mutated():
    original = {"api_key": "secret", "query": "hello"}
    redact_mapping(original)
    assert original["api_key"] == "secret"        # caller's data untouched


def test_safe_attributes_survive():
    attrs = {"query": "fusion", "results_count": 5, "tool": "web_search_v1"}
    assert redact_mapping(attrs) == attrs


# --- extensibility --------------------------------------------------------
def test_user_extra_denylist_key():
    out = redact_mapping({"ssn": "123-45-6789"}, extra_denylist_keys=["ssn"])
    assert out["ssn"] == REDACTION_PLACEHOLDER


def test_user_extra_pattern():
    internal = re.compile(r"INTERNAL-[0-9]+")
    out = redact_mapping(
        {"ref": "INTERNAL-99887766"}, extra_patterns=[internal]
    )
    assert out["ref"] == REDACTION_PLACEHOLDER
