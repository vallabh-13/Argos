"""Argos command-line surface — the guided onboarding layer (Phase C).

Nothing here is needed to *use* the SDK in code (``init_tracing`` / ``trace_step``
live in :mod:`argos.tracing`). This package is the human-facing front door:

* :mod:`argos.cli.setup`   — ``python -m argos setup`` prerequisite checker (C3).
* :mod:`argos.cli.menu`    — ``python -m argos`` interactive menu (C4+).
* :mod:`argos.cli.checks`  — reusable environment probes (Docker, AWS, ports).
* :mod:`argos.cli.console` — tiny, dependency-free colored output helpers.

Everything uses only the Python standard library, so the onboarding tools add no
runtime dependencies to the SDK.
"""
