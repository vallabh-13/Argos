#!/usr/bin/env python3
"""Argos bootstrap — the one command that works on a *fresh clone*.

`python -m argos setup` is the real prerequisite checker, but it can't run until
the Argos SDK (and its dependencies) are importable — and on a fresh clone they
aren't yet. That's the chicken-and-egg: "run setup first" can't work if setup
itself needs an install. This tiny **stdlib-only** script (it imports nothing
from `argos`) breaks the cycle:

  1. confirms Python is new enough (>= 3.10),
  2. nudges you toward a virtualenv if you're not in one,
  3. if the `argos` SDK isn't importable yet, offers to run
     `pip install -r requirements-all.txt` for you, then
  4. hands off to `python -m argos setup` for the full, colorized report.

Run it on a fresh clone with:

    python scripts/bootstrap.py   # or ./scripts/setup (macOS/Linux), scripts\setup.cmd (Windows)

Everything it does is safe and visible — it never installs system software, and
it asks before running pip.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

# This script lives in scripts/, so the repo root is its parent's parent. All
# the relative paths below (requirements-all.txt, backend/…, the pip cwd) and the
# hand-off to `python -m argos setup` are anchored on ROOT, so bootstrap works no
# matter which directory you launch it from.
ROOT = Path(__file__).resolve().parent.parent
REQUIREMENTS_ALL = ROOT / "requirements-all.txt"
MANUAL_INSTALL = (
    'pip install -e "sdk[kafka]" '
    "-r backend/requirements.txt -r examples/research-assistant/requirements.txt"
)


def _have_module(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def _activate_hint() -> str:
    return r".venv\Scripts\activate" if sys.platform == "win32" else "source .venv/bin/activate"


def main() -> int:
    print("Argos bootstrap - getting a fresh clone ready\n")

    # 1. Python version is a hard requirement.
    if sys.version_info < (3, 10):
        v = sys.version_info
        print(f"  [MISS] Python {v.major}.{v.minor} is too old; Argos needs >= 3.10.")
        print("         Install 3.10+ from https://www.python.org/downloads/ and re-run.")
        return 1
    print(f"  [ OK ] Python {sys.version_info.major}.{sys.version_info.minor}")

    # 2. Virtualenv nudge (warning only — we don't force it).
    in_venv = sys.prefix != getattr(sys, "base_prefix", sys.prefix)
    if in_venv:
        print("  [ OK ] Running inside a virtualenv")
    else:
        print("  [WARN] Not in a virtualenv. Recommended before installing:")
        print(f"         python -m venv .venv  &&  {_activate_hint()}")

    # 3. Is the SDK importable? If not, offer to install everything.
    if not _have_module("argos"):
        print("  [ -- ] The Argos SDK isn't installed in this environment yet.")
        if not REQUIREMENTS_ALL.is_file():
            print(f"         Couldn't find {REQUIREMENTS_ALL.name}; install manually then re-run:")
            print(f"         {MANUAL_INSTALL}")
            return 1
        try:
            answer = input(
                f"        Install everything now with "
                f"`pip install -r {REQUIREMENTS_ALL.name}`? [Y/n]: "
            ).strip().lower()
        except EOFError:
            answer = ""  # non-interactive: default to yes
        if answer in ("", "y", "yes"):
            rc = subprocess.call(
                [sys.executable, "-m", "pip", "install", "-r", str(REQUIREMENTS_ALL)],
                cwd=str(ROOT),
            )
            if rc != 0:
                print("\n  [MISS] pip install failed (see output above). Fix that, then re-run.")
                return rc
        else:
            print("         Skipped. Install yourself, then run:  python -m argos setup")
            print(f"         ({MANUAL_INSTALL})")
            return 0
    else:
        print("  [ OK ] Argos SDK is importable")

    # 4. Hand off to the real, full prerequisite checker.
    print("\nHanding off to the full prerequisite checker (`python -m argos setup`)...\n")
    return subprocess.call([sys.executable, "-m", "argos", "setup"], cwd=str(ROOT))


if __name__ == "__main__":
    raise SystemExit(main())
