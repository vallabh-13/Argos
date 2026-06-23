"""The LLM client the demo agents call — AWS Bedrock (Claude 3 Haiku) or a mock.

Two implementations behind one tiny interface (``.converse(system, messages)``
returning an :class:`LLMResult`):

* :class:`BedrockLLM` — calls the real Bedrock **Converse** API via boto3. Model
  id and region come from the environment (``ARGOS_BEDROCK_MODEL`` / ``AWS_REGION``),
  loaded from ``.env`` — never hardcoded. Credentials are NOT in ``.env``: boto3
  resolves them from the standard AWS credential chain (``aws configure`` locally,
  or an attached IAM role in EKS/EC2). It also handles the "this model needs an
  inference profile" Bedrock quirk gracefully (see :meth:`BedrockLLM.converse`).

* :class:`MockLLM` — returns canned, deterministic text with **no AWS call**.
  Enabled with ``ARGOS_BEDROCK_MOCK=1``. Lets you exercise the whole pipeline /
  dashboard with no credentials and no spend; the real agent logic (including the
  failure retry loop) still runs.

boto3 and python-dotenv are imported lazily so mock mode works even if neither is
installed.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

# The demo's .env lives next to this file; load it explicitly because the user
# runs the demo from the repo root, where load_dotenv()'s default search wouldn't
# find it.
_ENV_PATH = Path(__file__).parent / ".env"


def load_env() -> None:
    """Load this demo's ``.env`` into the environment (no-op if dotenv absent)."""

    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv(_ENV_PATH)


@dataclass
class LLMResult:
    """One model response: the text plus the token counts that drive cost."""

    text: str
    tokens_in: int
    tokens_out: int
    model: str


class MockLLM:
    """Offline stand-in for Bedrock. Deterministic, free, needs no credentials."""

    def __init__(self, model: str) -> None:
        self.model = model

    def describe(self) -> str:
        return f"MockLLM (offline, no AWS) posing as '{self.model}'"

    def converse(self, *, system: str, messages: list[dict[str, str]]) -> LLMResult:
        # Echo a trimmed view of the last user turn so plan/extract/summarize all
        # get usable, non-empty output. Token counts are estimated (~4 chars/token).
        user = messages[-1]["content"] if messages else ""
        text = f"[mock answer] {user.strip()[:200]}"
        tokens_in = max(1, (len(system or "") + len(user)) // 4)
        tokens_out = max(1, len(text) // 4)
        return LLMResult(text=text, tokens_in=tokens_in, tokens_out=tokens_out, model=self.model)


class BedrockLLM:
    """Calls Claude 3 Haiku via the Bedrock Converse API."""

    def __init__(
        self,
        model: str,
        region: str,
        *,
        max_tokens: int = 400,
        temperature: float = 0.2,
    ) -> None:
        import boto3  # lazy: only needed for real calls

        self.model = model
        self.region = region
        self.max_tokens = max_tokens   # capped to keep each call cheap
        self.temperature = temperature
        self._client = boto3.client("bedrock-runtime", region_name=region)

    def describe(self) -> str:
        return f"Bedrock '{self.model}' @ {self.region} (maxTokens={self.max_tokens})"

    def _invoke(self, model: str, system: str, messages: list[dict[str, str]]) -> dict[str, Any]:
        # Converse wants content as a list of typed blocks and system as its own
        # top-level list — translate our simple {role, content} messages to that.
        return self._client.converse(
            modelId=model,
            system=[{"text": system}] if system else [],
            messages=[
                {"role": m["role"], "content": [{"text": m["content"]}]} for m in messages
            ],
            inferenceConfig={"maxTokens": self.max_tokens, "temperature": self.temperature},
        )

    def converse(self, *, system: str, messages: list[dict[str, str]]) -> LLMResult:
        """Run one Converse call, handling the inference-profile quirk.

        Some Bedrock models can't be invoked by their bare foundation-model id and
        return a ValidationException telling you to use a *cross-region inference
        profile* (e.g. ``us.anthropic...``). Claude 3 Haiku in us-east-1 normally
        doesn't, but if it ever does we don't crash: we detect that specific error,
        retry once with the ``us.``-prefixed profile id, and tell the user how to
        make it permanent via ``ARGOS_BEDROCK_MODEL``.
        """

        from botocore.exceptions import ClientError

        model = self.model
        try:
            resp = self._invoke(model, system, messages)
        except ClientError as exc:
            msg = str(exc).lower()
            needs_profile = (
                "inference profile" in msg
                or "on-demand throughput isn" in msg
                or "with on-demand" in msg
            )
            if needs_profile and not model.startswith(("us.", "eu.", "apac.")):
                profile = f"us.{model}"
                print(
                    f"[bedrock] model '{model}' needs an inference profile; "
                    f"retrying once with '{profile}'.\n"
                    f"          To skip this next time, set ARGOS_BEDROCK_MODEL={profile}"
                )
                resp = self._invoke(profile, system, messages)
                self.model = model = profile  # remember it for the rest of the run
            else:
                raise

        text = resp["output"]["message"]["content"][0]["text"]
        usage = resp.get("usage", {})
        return LLMResult(
            text=text,
            tokens_in=int(usage.get("inputTokens", 0)),
            tokens_out=int(usage.get("outputTokens", 0)),
            model=model,
        )


def make_llm() -> Any:
    """Build the LLM client from the single config file, with env fallback.

    The model id and region come from ``argos.config.yml`` (``bedrock_model`` /
    ``aws_region``) — the one place Phase C puts non-secret settings. The legacy
    ``ARGOS_BEDROCK_MODEL`` / ``AWS_REGION`` env vars still win if set, so old
    ``.env`` setups keep working. A model id is required (no hardcoded default)
    so a misconfigured run fails loudly rather than calling the wrong model.
    """

    load_env()

    # config file supplies the baseline; env vars override it.
    try:
        from argos import load_config

        cfg = load_config()
    except ImportError:
        cfg = None

    model = os.getenv("ARGOS_BEDROCK_MODEL") or (cfg.bedrock_model if cfg else None)
    if not model:
        raise SystemExit(
            "No Bedrock model configured.\n"
            "Set 'bedrock_model' in argos.config.yml (copy argos.config.example.yml), "
            "or ARGOS_BEDROCK_MODEL in .env."
        )
    region = os.getenv("AWS_REGION") or (cfg.aws_region if cfg else None) or "us-east-1"

    if os.getenv("ARGOS_BEDROCK_MOCK", "").strip().lower() in ("1", "true", "yes"):
        return MockLLM(model=model)
    return BedrockLLM(model=model, region=region)
