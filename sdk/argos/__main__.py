"""``python -m argos`` — the onboarding entry point.

* ``python -m argos setup``  → the prerequisite checker (Phase C3).
* ``python -m argos``        → the guided interactive menu (Phase C4).

Kept thin on purpose: this only routes to the real implementations in
:mod:`argos.cli` so the command surface is easy to see at a glance.
"""

from __future__ import annotations

import sys
from typing import Optional


def main(argv: Optional[list[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)

    if argv and argv[0] == "setup":
        from .cli.setup import run_setup

        return run_setup(argv[1:])

    if not argv:
        try:
            from .cli.menu import run_menu
        except ImportError:
            # The menu (C4) isn't built yet — point at what does work.
            print("Argos CLI. Available now:\n    python -m argos setup")
            return 0
        return run_menu()

    print(f"Unknown command: {argv[0]!r}\nUsage: python -m argos [setup]")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
