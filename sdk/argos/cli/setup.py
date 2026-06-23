"""``python -m argos setup`` — the prerequisite checker (Phase C3).

It tells you, clearly, what's installed vs missing for running Argos, with
install guidance for anything absent. It then does the small, *safe* setup it
can do on its own:

* copies ``argos.config.example.yml`` → ``argos.config.yml`` if you don't have
  one yet (never overwrites an existing config), and
* checks the backend is *ready to start* — compose file valid, stack ports free —
  without actually starting it.

Deliberate non-goal: it never installs or upgrades system software (Docker,
Python, the AWS CLI). Auto-installing system packages is exactly the kind of
surprising, hard-to-undo action a setup script shouldn't take — so we report and
guide instead.
"""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path
from typing import Optional

from . import checks
from .checks import CheckResult, Status
from . import console


def _repo_root() -> Path:
    """Find the repo root (where argos.config.example.yml lives).

    Walk up from the current directory looking for the marker file; fall back to
    cwd. The config files live at the repo root, so that's our anchor.
    """

    here = Path.cwd().resolve()
    for directory in (here, *here.parents):
        if (directory / "argos.config.example.yml").is_file():
            return directory
    return here


def _render(result: CheckResult) -> None:
    marker = {
        Status.OK: console.MARK_OK,
        Status.WARN: console.MARK_WARN,
        Status.MISSING: console.MARK_MISS,
    }[result.status]
    console.line(marker, result.name, result.detail)
    if result.guidance and not result.ok:
        print("       " + console.dim(console.ARROW + " " + result.guidance))


def _ensure_config(root: Path) -> CheckResult:
    """Safe action: create argos.config.yml from the example if it's missing."""

    real = root / "argos.config.yml"
    example = root / "argos.config.example.yml"
    if real.is_file():
        return CheckResult("Config file", Status.OK, "argos.config.yml already present")
    if not example.is_file():
        return CheckResult(
            "Config file", Status.WARN, "argos.config.example.yml not found",
            guidance="Run setup from the repo root.",
        )
    shutil.copyfile(example, real)
    return CheckResult("Config file", Status.OK, "created argos.config.yml from the example")


def _check_ports() -> list[str]:
    """Report which stack ports are free vs already in use (informational)."""

    lines: list[str] = []
    for name, port in checks.STACK_PORTS.items():
        sep = console.DASH
        if checks.port_in_use(port):
            lines.append(f"{console.MARK_INFO} {name} :{port} {sep} in use (a service may already be running)")
        else:
            lines.append(f"{console.MARK_OK} {name} :{port} {sep} free")
    return lines


def run_setup(argv: Optional[list[str]] = None) -> int:
    """Run all checks + safe setup; print a report; return an exit code.

    Exit code is 0 when every *required* prerequisite (Python, Docker, the Docker
    daemon) is present, else 1. AWS being absent is a warning, not a failure —
    you can still exercise the whole pipeline in mock mode.
    """

    parser = argparse.ArgumentParser(
        prog="python -m argos setup",
        description="Check prerequisites and do the safe parts of Argos setup.",
    )
    parser.add_argument("--no-config", action="store_true",
                        help="don't create argos.config.yml from the example")
    args = parser.parse_args(argv)

    root = _repo_root()

    print(console.bold("Argos setup - checking your machine"))
    print(console.dim("Reports what's installed; never installs system software for you."))

    # 1. Core prerequisites.
    console.heading("Prerequisites")
    prereqs = [
        checks.check_python(),
        checks.check_docker(),
        checks.check_docker_compose(),
        checks.check_docker_daemon(),
    ]
    for r in prereqs:
        _render(r)

    # 2. AWS (optional — mock mode needs none of it).
    console.heading("AWS (optional - for real Bedrock runs)")
    aws_results = [checks.check_aws_cli(), checks.check_aws_credentials()]
    for r in aws_results:
        _render(r)

    # 3. Safe auto-setup.
    console.heading("Project setup")
    if args.no_config:
        console.line(console.MARK_INFO, "Config file", "skipped (--no-config)")
    else:
        _render(_ensure_config(root))
    _render(checks.check_compose_valid(str(root / "docker-compose.yml")))

    # 4. Backend readiness — ports, no `up`.
    console.heading("Backend readiness (ports - not starting anything)")
    for ln in _check_ports():
        print(ln)

    # 5. Verdict.
    required = {
        "Python >= 3.10": prereqs[0],
        "Docker": prereqs[1],
        "Docker daemon": prereqs[3],
    }
    missing_required = [name for name, r in required.items() if r.status is Status.MISSING]
    daemon_down = required["Docker daemon"].status is Status.WARN

    console.heading("Summary")
    if missing_required:
        console.line(console.MARK_MISS, "Not ready",
                     "missing: " + ", ".join(missing_required))
        print(console.dim("       Install the items above, then re-run `python -m argos setup`."))
        return 1

    if daemon_down:
        console.line(console.MARK_WARN, "Almost ready",
                     "Docker is installed but the daemon isn't running")
        print(console.dim("       Start Docker, then run `python -m argos` and pick "
                          "'Start backend'."))
        return 0

    any_aws_warn = any(r.status is Status.WARN for r in aws_results)
    detail = "all prerequisites present"
    if any_aws_warn:
        detail += " (AWS not configured - mock mode still works)"
    console.line(console.MARK_OK, "Ready", detail)
    print(console.dim("       Next: `python -m argos` -> 'Start backend', then 'Run demo'."))
    return 0
