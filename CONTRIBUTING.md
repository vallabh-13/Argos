# Contributing to Argos

Thanks for your interest — Argos is built in public on purpose, and
contributions are welcome.

Argos is **OpenTelemetry-native distributed tracing for multi-agent AI
systems**. The product is the tracing system (SDK + backend pipeline +
dashboard); the agents under `examples/` exist only as something to trace.

## Before you start

- **Open an issue first** for anything non-trivial, so we can align on direction
  before you write code. Use the bug / feature templates.
- Good first issues are labeled **`good-first-issue`**.
- All contributions are licensed under **Apache 2.0** (see [`LICENSE`](LICENSE)).

## Development setup

> The full stack is still being built phase by phase (see
> [`docs/PROJECT.md`](docs/PROJECT.md) → Implementation Plan). For now:

```bash
# 1. Clone your fork
git clone https://github.com/<you>/argos.git
cd argos

# 2. Create and activate a virtual environment
python -m venv .venv
# Windows:  .venv\Scripts\activate
# macOS/Linux:  source .venv/bin/activate

# 3. Install test dependencies
pip install pytest

# 4. Run the test suite (must stay green)
pytest -v
```

The full local stack will come up with `docker compose up` once the relevant
phases land. Today that command is an intentional no-op (skeleton only).

## Dev workflow

1. Create a branch off `main`: `git checkout -b feature/short-description`.
2. Make your change. Keep it focused — one logical change per PR.
3. Match the style and comment density of the surrounding code.
4. Ensure `pytest -v` passes locally; CI runs the same on every push.
5. Open a pull request describing **what** changed and **why**.

## Non-negotiables

- **Security first:** the SDK must redact secrets (API keys, tokens, passwords)
  *before* a span leaves the user's machine. Never log or store raw secrets.
- **Adoption simplicity:** wrapping someone's agents must stay 2–3 lines.
- Every span carries a shared **trace_id** so spans can be reassembled later.
- Keep CI green on every push.

## Code of conduct

Be respectful and constructive. We're here to build something useful and learn
in the open.
