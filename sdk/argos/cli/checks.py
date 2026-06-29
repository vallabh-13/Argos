"""Reusable environment probes for the Argos onboarding tools.

Each probe answers one yes/no-ish question about the machine — "is Docker
installed?", "is the daemon running?", "is port 29092 free?" — and returns a
:class:`CheckResult` carrying a status, a human detail string, and (when
something's wrong) install/fix guidance. The setup checker (C3) renders a column
of these; the menu's "Start backend" step (C4) reuses the Docker/port probes for
its health check.

Everything here is standard-library only and side-effect free (it never installs
or starts anything) — except the explicitly-named safe actions in
:mod:`argos.cli.setup`.
"""

from __future__ import annotations

import importlib.util
import platform
import shutil
import socket
import subprocess
import sys
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Optional


def repo_root() -> Path:
    """Find the Argos repo root (where argos.config.example.yml lives).

    Walk up from the current directory looking for that marker; fall back to the
    cwd. Shared by the setup checker and the menu so both anchor on the same
    place when invoking docker compose / the demo.
    """

    here = Path.cwd().resolve()
    for directory in (here, *here.parents):
        if (directory / "argos.config.example.yml").is_file():
            return directory
    return here


class Status(str, Enum):
    OK = "ok"        # present / healthy
    WARN = "warn"    # missing-but-optional, or a soft problem
    MISSING = "missing"  # required and absent


@dataclass
class CheckResult:
    name: str
    status: Status
    detail: str = ""
    guidance: str = ""

    @property
    def ok(self) -> bool:
        return self.status is Status.OK


# --- low-level helpers ----------------------------------------------------
def _run(cmd: list[str], timeout: float = 8.0) -> tuple[int, str]:
    """Run a command, returning (returncode, combined-output). Never raises.

    A missing binary, non-zero exit, or timeout all collapse into a non-zero
    return code so callers can branch on success without try/except noise.
    """

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return proc.returncode, (proc.stdout or "") + (proc.stderr or "")
    except FileNotFoundError:
        return 127, "not found"
    except subprocess.TimeoutExpired:
        return 124, "timed out"
    except OSError as exc:  # noqa: BLE001 - report, don't crash the checker
        return 1, str(exc)


def _first_line(text: str) -> str:
    return text.strip().splitlines()[0].strip() if text.strip() else ""


def _os_name() -> str:
    return {"win32": "windows", "darwin": "macos"}.get(sys.platform, "linux")


# --- prerequisite probes --------------------------------------------------
def check_python() -> CheckResult:
    v = sys.version_info
    detail = f"Python {v.major}.{v.minor}.{v.micro}"
    if (v.major, v.minor) >= (3, 10):
        return CheckResult("Python >= 3.10", Status.OK, detail)
    return CheckResult(
        "Python >= 3.10",
        Status.MISSING,
        f"{detail} is too old",
        guidance="Install Python 3.10+ from https://www.python.org/downloads/",
    )


def check_docker() -> CheckResult:
    if shutil.which("docker") is None:
        return CheckResult(
            "Docker",
            Status.MISSING,
            "not found on PATH",
            guidance=_docker_guidance(),
        )
    rc, out = _run(["docker", "--version"])
    if rc != 0:
        return CheckResult("Docker", Status.MISSING, _first_line(out) or "not runnable",
                           guidance=_docker_guidance())
    return CheckResult("Docker", Status.OK, _first_line(out))


def check_docker_compose() -> CheckResult:
    # Modern Docker ships compose as a subcommand: `docker compose version`.
    if shutil.which("docker") is None:
        return CheckResult("Docker Compose", Status.MISSING, "Docker not installed",
                           guidance=_docker_guidance())
    rc, out = _run(["docker", "compose", "version"])
    if rc == 0:
        return CheckResult("Docker Compose", Status.OK, _first_line(out))
    return CheckResult(
        "Docker Compose",
        Status.MISSING,
        "`docker compose` unavailable",
        guidance="Update Docker Desktop, or install the Compose v2 plugin.",
    )


def check_docker_daemon() -> CheckResult:
    """Is the Docker daemon actually running (not just installed)?"""

    if shutil.which("docker") is None:
        return CheckResult("Docker daemon", Status.MISSING, "Docker not installed",
                           guidance=_docker_guidance())
    rc, out = _run(["docker", "info", "--format", "{{.ServerVersion}}"], timeout=12.0)
    if rc == 0 and out.strip():
        return CheckResult("Docker daemon", Status.OK, f"running (server {_first_line(out)})")
    return CheckResult(
        "Docker daemon",
        Status.WARN,
        "installed but not running",
        guidance="Start Docker Desktop (or `sudo systemctl start docker`) before "
                 "bringing the backend up.",
    )


def check_aws_cli() -> CheckResult:
    if shutil.which("aws") is None:
        return CheckResult(
            "AWS CLI",
            Status.WARN,  # optional: mock mode needs no AWS
            "not found on PATH",
            guidance=_aws_guidance(),
        )
    rc, out = _run(["aws", "--version"])
    if rc != 0:
        return CheckResult("AWS CLI", Status.WARN, "installed but not runnable",
                           guidance=_aws_guidance())
    return CheckResult("AWS CLI", Status.OK, _first_line(out))


def check_aws_credentials() -> CheckResult:
    """Are AWS credentials configured? Local check only — no network/STS call.

    ``aws configure list`` reads the resolved config (keys, profile, region)
    without contacting AWS, so this is fast, free, and can't hang. We don't print
    the values — only whether an access key resolved from *somewhere* in the
    chain (env, profile, or IAM role).
    """

    if shutil.which("aws") is None:
        return CheckResult("AWS credentials", Status.WARN, "AWS CLI not installed",
                           guidance=_aws_guidance())
    rc, out = _run(["aws", "configure", "list"])
    if rc != 0:
        return CheckResult("AWS credentials", Status.WARN, "could not read config",
                           guidance="Run `aws configure` to set up credentials.")
    # In the table, the access_key row shows "<not set>" when nothing resolved.
    configured = False
    for row in out.splitlines():
        if row.strip().startswith("access_key") and "<not set>" not in row:
            configured = True
            break
    if configured:
        return CheckResult("AWS credentials", Status.OK, "resolved from the credential chain")
    return CheckResult(
        "AWS credentials",
        Status.WARN,
        "none configured",
        guidance="Run `aws configure` (or set ARGOS_BEDROCK_MOCK=1 to skip AWS).",
    )


def check_aws_identity() -> CheckResult:
    """Resolve the caller's AWS identity via STS — a real network call.

    Unlike :func:`check_aws_credentials` (local only), this proves the resolved
    credentials actually work by asking STS "who am I?", and returns the IAM ARN
    so the menu can show a friendly "Connected as ..." confirmation. Any failure
    (no creds, no network, bad keys) collapses to a WARN with guidance — it never
    raises.
    """

    if shutil.which("aws") is None:
        return CheckResult("AWS identity", Status.WARN, "AWS CLI not installed",
                           guidance=_aws_guidance())
    rc, out = _run(
        ["aws", "sts", "get-caller-identity", "--query", "Arn", "--output", "text"],
        timeout=12.0,
    )
    arn = _first_line(out)
    if rc == 0 and arn and arn.lower() != "none":
        return CheckResult("AWS identity", Status.OK, arn)
    return CheckResult(
        "AWS identity",
        Status.WARN,
        "could not resolve an identity",
        guidance="Run `aws configure` (or check your network/keys); "
                 "set ARGOS_BEDROCK_MOCK=1 to skip AWS entirely.",
    )


def bedrock_access_note() -> str:
    """One-line reminder that Bedrock model access is separate from credentials."""

    return ("Bedrock also needs MODEL ACCESS enabled for your model in the AWS console "
            "(Bedrock -> Model access) - that's separate from `aws configure` creds.")


# --- Python-package probes ------------------------------------------------
# Packages the backend + demo need beyond the SDK's own deps. Console-only mode
# works without them, so each absence is a WARN (not a hard failure) - but the
# ingest consumer, the dashboard round-trip, and real Bedrock runs all need them.
# (pip name, import name, what it's for).
PYTHON_PACKAGES: list[tuple[str, str, str]] = [
    ("confluent-kafka", "confluent_kafka", "SDK Kafka sink + the ingest consumer"),
    ("clickhouse-connect", "clickhouse_connect", "consumer writes + the 'verify' step read ClickHouse"),
    ("boto3", "boto3", "the demo agents call AWS Bedrock"),
]


def check_python_package(pip_name: str, import_name: str, purpose: str) -> CheckResult:
    """Is an importable package present? Reports a WARN with install guidance if not.

    Uses ``importlib.util.find_spec`` so we never actually import (and possibly
    run) the package - we only ask whether Python could find it.
    """

    try:
        found = importlib.util.find_spec(import_name) is not None
    except (ImportError, ValueError):  # a broken/namespace package shouldn't crash us
        found = False
    if found:
        return CheckResult(pip_name, Status.OK, "installed")
    return CheckResult(
        pip_name,
        Status.WARN,
        f"not installed - needed so {purpose}",
        guidance="Install everything in one go:  pip install -r requirements-all.txt",
    )


def check_python_packages() -> list[CheckResult]:
    """Run all the backend/demo package probes, in declaration order."""

    return [check_python_package(*spec) for spec in PYTHON_PACKAGES]


# --- backend-readiness probes (used by setup C3 and menu C4) --------------
# The stack's host ports, from docker-compose.yml. Name → port.
STACK_PORTS: dict[str, int] = {
    "Kafka": 29092,
    "ClickHouse HTTP": 8123,
    "ClickHouse native": 9000,
    "Prometheus": 9090,
    "Grafana": 3000,
}


def port_in_use(port: int, host: str = "127.0.0.1", timeout: float = 0.5) -> bool:
    """True if something is already listening on ``host:port`` (a TCP connect
    succeeds). Used both to spot conflicts before `up` and to confirm a service
    came up after it."""

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout)
        return sock.connect_ex((host, port)) == 0


def http_ok(url: str, timeout: float = 1.5) -> bool:
    """True if an HTTP GET to ``url`` returns a 2xx status. Stdlib only.

    A TCP port opening doesn't mean a service can answer yet (ClickHouse opens
    :8123 seconds before it serves queries). This is the stronger 'really ready'
    probe used for the menu's health check.
    """

    import urllib.error
    import urllib.request

    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return 200 <= resp.status < 300
    except (urllib.error.URLError, OSError):
        return False


def clickhouse_ready(host: str = "localhost", port: int = 8123) -> bool:
    """True once ClickHouse can actually serve (its /ping returns 200)."""

    return http_ok(f"http://{host}:{port}/ping")


def kafka_ready(bootstrap: str = "localhost:29092", timeout: float = 4.0) -> bool:
    """True once the Kafka broker actually answers (not just an open port).

    The broker's port opens several seconds before it can serve produce/metadata
    requests, so a producer that fires too early silently drops its messages. We
    ask the broker for cluster metadata via the admin client — that only succeeds
    once it's genuinely ready. Falls back to a port check if confluent-kafka isn't
    installed (e.g. an SDK-only environment).
    """

    try:
        from confluent_kafka.admin import AdminClient
    except ImportError:
        return port_in_use(29092)
    try:
        metadata = AdminClient({"bootstrap.servers": bootstrap}).list_topics(timeout=timeout)
        return metadata is not None
    except Exception:  # noqa: BLE001 - broker not up yet / connection refused
        return False


def check_compose_valid(compose_file: Optional[str] = None) -> CheckResult:
    """Validate the compose file parses, without starting anything."""

    if shutil.which("docker") is None:
        return CheckResult("Compose file", Status.WARN, "Docker not installed",
                           guidance=_docker_guidance())
    cmd = ["docker", "compose"]
    if compose_file:
        cmd += ["-f", compose_file]
    cmd += ["config", "-q"]
    rc, out = _run(cmd, timeout=15.0)
    if rc == 0:
        return CheckResult("Compose file", Status.OK, "valid")
    return CheckResult("Compose file", Status.WARN, "could not validate",
                       guidance=_first_line(out) or "Check docker-compose.yml syntax.")


# --- platform-specific guidance strings -----------------------------------
def _docker_guidance() -> str:
    return {
        "windows": "Install Docker Desktop: https://docs.docker.com/desktop/install/windows-install/",
        "macos": "Install Docker Desktop: https://docs.docker.com/desktop/install/mac-install/",
        "linux": "Install Docker Engine: https://docs.docker.com/engine/install/",
    }[_os_name()]


def _aws_guidance() -> str:
    return {
        "windows": "Install the AWS CLI: https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html",
        "macos": "Install the AWS CLI: `brew install awscli` or https://aws.amazon.com/cli/",
        "linux": "Install the AWS CLI: https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html",
    }[_os_name()]
