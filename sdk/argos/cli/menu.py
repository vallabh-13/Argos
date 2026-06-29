"""``python -m argos`` — the guided interactive console (Phase C4 + C5).

This is the onboarding centerpiece: a numbered menu that walks someone from "I
just cloned this" to "I can see my traces", without them needing to remember any
commands. Each option is a small, self-contained guided step.

    1) Connect AWS        - run `aws configure`, then verify creds resolved
    2) Start backend      - `docker compose up -d`, health-checked
    3) Instrument my agents - the one code change (Phase C5)
    4) Run demo           - the bundled multi-agent demo (happy / fail)
    5) Open dashboard     - launch Grafana in your browser
    6) Settings           - view the current argos.config.yml

Design choices:
* We never hide what we're doing — each option says what command it runs and
  why before running it.
* Heavy/interactive sub-processes (aws configure, docker compose, the demo) run
  with inherited stdio so the user sees real output, not a captured summary.
* Everything anchors on :func:`argos.cli.checks.repo_root` so it works no matter
  which subdirectory you launched from.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
import webbrowser
from pathlib import Path
from typing import Callable, Optional

from . import checks, console
from .checks import Status

DASHBOARD_URL = "http://localhost:3000"

# Handles to the background services this menu started this session, keyed by
# name, so re-entering "Start backend" doesn't spawn duplicates. A per-service
# PID file (below) covers the cross-session case where the menu was restarted but
# a service kept running.
_bg_procs: "dict[str, subprocess.Popen]" = {}

# The long-lived background services "Start backend" launches. Each entry is the
# ``python -m ...`` args that run it plus a one-line note on why the dashboard
# needs it:
#   * consumer — drains Kafka into ClickHouse, so spans land at all.
#   * detector — scores each new trace AND writes it into argos.trace_nodes, the
#     table the Grafana detail / timeline / sequence panels read from. Without it
#     those panels stay empty even though spans are arriving.
_SERVICES: "dict[str, tuple[list[str], str]]" = {
    "consumer": (
        ["-m", "backend.ingest.consumer"],
        "drains Kafka -> ClickHouse so spans reach the dashboard",
    ),
    "detector": (
        ["-m", "backend.detection", "--watch"],
        "scores new traces + fills argos.trace_nodes for the detail panels",
    ),
}


# --- first-run: make sure a config exists ---------------------------------
def _ensure_config_present(root: Path) -> None:
    """If argos.config.yml is missing, offer to create it from the example.

    Without the config the whole flow runs console-only — init_tracing() has no
    backend, so spans never reach ClickHouse and the dashboard stays empty. We'd
    rather catch that on the way in than let the user wonder why option 5 shows
    nothing.
    """

    config = root / "argos.config.yml"
    if config.is_file():
        return

    console.heading("First-time setup - no argos.config.yml yet")
    print("Argos reads its (non-secret) settings from argos.config.yml, and you don't")
    print("have one yet. Without it, spans run CONSOLE-ONLY and never reach the dashboard.")

    example = root / "argos.config.example.yml"
    if not example.is_file():
        console.line(console.MARK_WARN, "No example to copy from",
                     "run this from the repo root so the example is found")
        return

    if console.confirm("Create argos.config.yml from the example now?", default=True):
        shutil.copyfile(example, config)
        console.line(console.MARK_OK, "Created argos.config.yml",
                     "edit it any time (option 6), or via option 3")
    else:
        print(console.dim("Skipped - the demo will run console-only until you create it."))


# --- option 1: Connect AWS ------------------------------------------------
def connect_aws() -> None:
    console.heading("1) Connect AWS")
    print("Argos reads AWS credentials from the standard credential chain - no keys")
    print("in any file. The simplest way to set that up is the AWS CLI's own wizard.")
    print(console.dim("This runs:  aws configure"))

    cli = checks.check_aws_cli()
    if cli.status is not Status.OK and cli.guidance:
        console.line(console.MARK_WARN, "AWS CLI not available", cli.detail)
        print("       " + console.dim(console.ARROW + " " + cli.guidance))
        return

    if not console.confirm("Run `aws configure` now?", default=True):
        print(console.dim("Skipped."))
        return

    # Interactive: inherit stdio so the user answers the prompts directly.
    subprocess.run(["aws", "configure"])

    # Confirm by asking STS who we actually are — proves the creds resolve AND
    # gives us a concrete identity to echo back, instead of a vague "done".
    identity = checks.check_aws_identity()
    print()
    if identity.status is Status.OK:
        print(console.green(f"{console.CHECK} Connected as {identity.detail}"))
        print("   Next: start the backend (option 2), then run the demo (option 4).")
        # Credentials alone aren't enough for Bedrock — model access is a separate grant.
        print("   " + console.dim(console.ARROW + " Bedrock also needs MODEL ACCESS "
                                  "(console -> Bedrock -> Model access), separate from creds."))
    else:
        console.line(console.MARK_WARN, "AWS not connected", identity.detail)
        if identity.guidance:
            print("       " + console.dim(console.ARROW + " " + identity.guidance))


# --- option 2: Start backend ----------------------------------------------
def start_backend(root: Path) -> None:
    console.heading("2) Start backend")
    print("Brings up the whole local stack (Kafka, ClickHouse, Prometheus, Grafana).")
    print(console.dim("This runs:  docker compose up -d"))

    daemon = checks.check_docker_daemon()
    if daemon.status is not Status.OK:
        console.line(console.MARK_WARN, daemon.name, daemon.detail)
        if daemon.guidance:
            print("       " + console.dim(console.ARROW + " " + daemon.guidance))
        return

    if not console.confirm("Start the backend now?", default=True):
        print(console.dim("Skipped."))
        return

    proc = subprocess.run(["docker", "compose", "up", "-d"], cwd=str(root))
    if proc.returncode != 0:
        console.line(console.MARK_MISS, "docker compose up", "failed (see output above)")
        return

    print("\nWaiting for services to become reachable...")
    statuses = _wait_for_backend()
    for name, up in statuses.items():
        console.line(console.MARK_OK if up else console.MARK_WARN,
                     name, "ready" if up else "not reachable yet")

    # The consumer + detector are the missing links: docker compose starts Kafka,
    # ClickHouse and Grafana, but the dashboard only fills in once (a) something
    # drains the topic into ClickHouse — the consumer — AND (b) something writes
    # the assembled traces into argos.trace_nodes — the detector's --watch. We
    # start BOTH for the user so the dashboard works with no extra terminals,
    # which is the whole point of a guided menu.
    consumer_ok = detector_ok = False
    if statuses.get("Kafka :29092") and statuses.get("ClickHouse :8123"):
        consumer_ok = _ensure_consumer(root)
        detector_ok = _ensure_detector(root)
    else:
        console.line(console.MARK_WARN, "Skipping background services",
                     "Kafka/ClickHouse not both up yet - re-run option 2 once they are")

    # One clear confirmation that states what's running and what to do next.
    print()
    if all(statuses.values()) and consumer_ok and detector_ok:
        print(console.green(f"{console.CHECK} Backend is up {console.DASH} Kafka, ClickHouse, "
                            "Grafana, the ingest consumer, and the detector are running."))
        print(f"   Dashboard: {DASHBOARD_URL} {console.DASH} next: run the demo (option 4) "
              "or instrument your agents (option 3).")
    else:
        console.line(console.MARK_WARN, "Backend not fully ready yet",
                     "give it a few more seconds, or check `docker compose ps` "
                     "and the logs in .argos/")


# --- background services: started by "Start backend", outlive the menu --------
# Spawned DETACHED so they keep running after you press Enter to return to the
# menu, and tracked by a PID file so a menu restart re-attaches instead of
# spawning a duplicate. The consumer and the detector watcher share this code
# path — see _SERVICES above for what each one is.
def _runtime_dir(root: Path) -> Path:
    """A small repo-local dir for service PID + log files (gitignored)."""

    d = root / ".argos"
    d.mkdir(exist_ok=True)
    return d


def _pidfile(root: Path, name: str) -> Path:
    return _runtime_dir(root) / f"{name}.pid"


def _logfile(root: Path, name: str) -> Path:
    return _runtime_dir(root) / f"{name}.log"


def _read_pid(root: Path, name: str) -> Optional[int]:
    pidfile = _pidfile(root, name)
    if not pidfile.is_file():
        return None
    try:
        return int(pidfile.read_text(encoding="utf-8").strip())
    except (ValueError, OSError):
        return None


def _pid_alive(pid: Optional[int]) -> bool:
    """Is a process with this PID running? Cross-platform, no extra deps.

    On Windows ``os.kill(pid, 0)`` would *terminate* the process (it maps to
    TerminateProcess), so we ask ``tasklist`` instead. On POSIX, signal 0 is the
    standard 'does this pid exist?' probe.
    """

    if pid is None:
        return False
    if sys.platform == "win32":
        out = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
            capture_output=True, text=True,
        )
        return str(pid) in out.stdout
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _already_running(root: Path, name: str) -> Optional[int]:
    """PID of a live instance of ``name`` (this session or a prior one), else None."""

    proc = _bg_procs.get(name)
    if proc is not None and proc.poll() is None:
        return proc.pid
    pid = _read_pid(root, name)
    return pid if _pid_alive(pid) else None


def _manual_hint(name: str) -> str:
    cmd = " ".join(["python", *_SERVICES[name][0]])
    return (f"Start it manually:  {cmd}   "
            "(deps: pip install -r backend/requirements.txt)")


def _stop_hint(pid: int) -> str:
    return f"taskkill /PID {pid} /F" if sys.platform == "win32" else f"kill {pid}"


def _tail(path: Path, lines: int) -> str:
    try:
        content = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    return "\n".join("       " + ln for ln in content[-lines:])


def _ensure_service(root: Path, name: str) -> bool:
    """Start background service ``name`` if it isn't already running.

    Returns True when a live instance is running by the time we return (already
    up or freshly started), False if we couldn't get one going — the caller uses
    this to decide whether the "Backend is up" confirmation is honest.
    """

    args, what = _SERVICES[name]
    label = name.capitalize()

    print()
    print(f"{label} ({what}):")

    running = _already_running(root, name)
    if running is not None:
        console.line(console.MARK_OK, f"{label} already running", f"pid {running}")
        return True

    log = _logfile(root, name)
    try:
        logf = open(log, "a", encoding="utf-8")
        stdout_target: object = logf
    except OSError:
        logf = None
        stdout_target = subprocess.DEVNULL

    # Detach so the service outlives this menu process and has no console window.
    spawn_kwargs: dict = {}
    if sys.platform == "win32":
        spawn_kwargs["creationflags"] = subprocess.DETACHED_PROCESS
    else:
        spawn_kwargs["start_new_session"] = True

    # Unbuffered, so the log file updates live (a detached process otherwise
    # block-buffers stdout and the log looks empty even while it's working).
    env = dict(os.environ)
    env["PYTHONUNBUFFERED"] = "1"

    try:
        proc = subprocess.Popen(
            [sys.executable, "-u", *args],
            cwd=str(root),
            stdin=subprocess.DEVNULL,
            stdout=stdout_target,
            stderr=subprocess.STDOUT,
            env=env,
            **spawn_kwargs,
        )
    except Exception as exc:  # noqa: BLE001 - report and fall back to manual
        console.line(console.MARK_WARN, f"Couldn't start {name}", str(exc))
        print("       " + console.dim(_manual_hint(name)))
        return False
    finally:
        if logf is not None:
            logf.close()

    _bg_procs[name] = proc
    try:
        _pidfile(root, name).write_text(str(proc.pid), encoding="utf-8")
    except OSError:
        pass

    # Give it a beat to fall over on missing deps / a bad connection, so we can
    # report a real failure instead of a falsely cheerful "started".
    time.sleep(2.5)
    if proc.poll() is not None:
        console.line(console.MARK_WARN, f"{label} exited immediately", f"see {log}")
        tail = _tail(log, 6)
        if tail:
            print(console.dim(tail))
        print("       " + console.dim(_manual_hint(name)))
        return False

    console.line(console.MARK_OK, f"{label} started",
                 f"pid {proc.pid}, logging to .argos/{log.name}")
    print(console.dim(f"       Runs in the background. Stop it with:  {_stop_hint(proc.pid)}"))
    return True


def _ensure_consumer(root: Path) -> bool:
    """Start the ingest consumer (Kafka -> ClickHouse) in the background."""

    return _ensure_service(root, "consumer")


def _ensure_detector(root: Path) -> bool:
    """Start the detector watcher in the background.

    Besides scoring new traces for the alert metrics, ``--watch`` is what writes
    each assembled trace into ``argos.trace_nodes`` — the table the Grafana
    detail / timeline / sequence panels read from. Without it those panels stay
    empty, so it's started right alongside the consumer.
    """

    return _ensure_service(root, "detector")


def _backend_statuses() -> dict[str, bool]:
    """One snapshot of whether each user-facing service is *really* ready.

    ClickHouse gets the stronger HTTP /ping probe (its port opens well before it
    can serve queries); Kafka and Grafana use a port check, which is enough to
    know they're accepting connections.
    """

    return {
        "Kafka :29092": checks.kafka_ready(),
        "ClickHouse :8123": checks.clickhouse_ready(),
        "Grafana :3000": checks.port_in_use(3000),
    }


def _wait_for_backend(timeout: float = 120.0) -> dict[str, bool]:
    """Poll until every service is ready or we time out, returning the last snapshot."""

    deadline = time.time() + timeout
    statuses = _backend_statuses()
    while time.time() < deadline:
        statuses = _backend_statuses()
        if all(statuses.values()):
            return statuses
        time.sleep(2.0)
    return statuses


# --- option 3: Instrument my agents (Phase C5) ----------------------------
def instrument_agents(root: Path) -> None:
    # Delegated to its own module — it's the most involved flow (show/place/verify).
    from .instrument import instrument_agents as _run

    _run(root)


# --- option 4: Run demo ---------------------------------------------------
def _warn_if_spans_wont_land() -> bool:
    """Warn if the demo's spans won't reach ClickHouse/the dashboard.

    Two failure modes, both silent otherwise: (a) no backend configured at all
    (console-only), and (b) a backend is configured but nothing is listening on
    it yet. Returns True to proceed, False if the user chose to bail.
    """

    from argos import load_config

    cfg = load_config()
    if not cfg.has_backend:
        console.line(console.MARK_WARN, "No span backend configured",
                     "spans will print to the console only - they won't reach the dashboard")
        print("       " + console.dim("Set 'backend: localhost:29092' in argos.config.yml "
                                       "(option 6) to pipe them into the pipeline."))
        return console.confirm("Run console-only anyway?", default=True)

    if not checks.kafka_ready():
        console.line(console.MARK_WARN, "Backend not reachable",
                     f"nothing is answering on {cfg.backend} - spans may be dropped")
        print("       " + console.dim("Start it with option 2 first, then re-run this."))
        return console.confirm("Run anyway (spans may be lost)?", default=False)

    return True


def run_demo(root: Path) -> None:
    console.heading("4) Run demo")
    print("Runs the bundled multi-agent demo so real spans flow through the pipeline.")
    print(console.dim("happy = clean run    fail = agents loop and trip the detectors"))

    # Don't let spans silently vanish: if there's no reachable backend, the demo
    # works but its spans only print to the console - they never reach ClickHouse
    # or the dashboard. Warn (don't block) so the user makes an informed choice.
    if not _warn_if_spans_wont_land():
        return

    scenario = console.prompt("Scenario (happy/fail)", default="happy").strip().lower()
    if scenario not in ("happy", "fail"):
        console.line(console.MARK_WARN, "Unknown scenario", f"'{scenario}' - using 'happy'")
        scenario = "happy"

    aws_ok = checks.check_aws_credentials().status is Status.OK
    # Default to mock when AWS isn't set up, so the demo always works.
    use_mock = console.confirm("Use mock LLM (no AWS calls / no spend)?", default=not aws_ok)

    script = root / "examples" / "research-assistant" / "run_demo.py"
    if not script.is_file():
        console.line(console.MARK_MISS, "Demo not found", str(script))
        return

    env = dict(os.environ)
    env["ARGOS_BEDROCK_MOCK"] = "1" if use_mock else "0"

    cmd = [sys.executable, str(script), "--scenario", scenario]
    print(console.dim(f"\nRunning:  {' '.join(cmd)}  (mock={use_mock})\n"))
    proc = subprocess.run(cmd, cwd=str(root), env=env)
    if proc.returncode != 0:
        console.line(console.MARK_WARN, "Demo exited non-zero", "see the output above")
        return

    _confirm_demo_done()


def _confirm_demo_done() -> None:
    """State that the demo ran and, crucially, WHOSE service its spans carry.

    The demo calls init_tracing() with no args, so its spans are tagged with the
    service_name in argos.config.yml. If the user has wrapped their own agent and
    set their own name, the demo reuses it — we say so, plainly, rather than
    pretending the demo is always 'research-assistant'.
    """

    from argos import load_config

    cfg = load_config()
    service = cfg.service_name or "research-assistant"
    lands = cfg.has_backend and checks.kafka_ready()

    print()
    if not lands:
        print(console.green(f"{console.CHECK} Demo ran {console.DASH} spans printed to the console "
                            "above (no reachable backend)."))
        print("   To see it in Grafana: set a backend (option 3 or 6), then start the backend (option 2).")
        return

    if service == "research-assistant":
        print(console.green(f"{console.CHECK} Demo trace sent {console.DASH} the bundled "
                            f"research-assistant demo ran (spans tagged service '{service}')."))
        print("   That's the example, not your own agent. See it in Grafana (option 5).")
    else:
        print(console.green(f"{console.CHECK} Demo trace sent {console.DASH} the bundled demo ran, but "
                            f"its spans are tagged with YOUR service '{service}' (from the config)."))
        print("   So in Grafana this demo mixes in with your own agent's traces. See it in Grafana (option 5).")


# --- option 5: Open dashboard ---------------------------------------------
def open_dashboard() -> None:
    console.heading("5) Open dashboard")
    if not checks.port_in_use(3000):
        console.line(console.MARK_WARN, "Grafana not reachable",
                     "is the backend running? (menu option 2)")
        # Still offer to open it in case it's just starting.
        if not console.confirm(f"Open {DASHBOARD_URL} anyway?", default=False):
            return

    service, _ = _current_service()
    if service != "(unset)":
        print(console.dim(f"Tip: in Grafana, filter service = '{service}' to pick out those traces."))

    print(f"Opening {console.cyan(DASHBOARD_URL)} ...")
    try:
        opened = webbrowser.open(DASHBOARD_URL)
    except Exception:  # noqa: BLE001 - headless / no browser configured
        opened = False
    if not opened:
        console.line(console.MARK_WARN, "Couldn't launch a browser",
                     f"open this URL manually: {DASHBOARD_URL}")


# --- option 6: Settings ---------------------------------------------------
def show_settings() -> None:
    console.heading("6) Settings")
    from argos import load_config

    cfg = load_config()
    source = str(cfg.source) if cfg.source else "(none found - using defaults)"
    print(f"Config source: {console.cyan(source)}")
    console.line(console.MARK_INFO, "service_name", cfg.service_name or "(unset)")
    console.line(console.MARK_INFO, "backend", cfg.backend or "(console - no Kafka)")
    console.line(console.MARK_INFO, "bedrock_model", cfg.bedrock_model or "(unset)")
    console.line(console.MARK_INFO, "aws_region", cfg.aws_region)
    console.line(console.MARK_INFO, "detection.loop_count", str(cfg.detection.loop_count))
    console.line(console.MARK_INFO, "detection.failure_count", str(cfg.detection.failure_count))
    console.line(console.MARK_INFO, "detection.cost_limit_usd", str(cfg.detection.cost_limit_usd))
    print(console.dim("\nEdit these by changing argos.config.yml directly."))


# --- the menu loop --------------------------------------------------------
def _banner() -> None:
    print()
    print(console.bold("=== Argos - guided console ==="))
    print(console.dim("OpenTelemetry-native tracing for multi-agent AI systems"))


def _current_service() -> tuple[str, str]:
    """The configured service name + a plain note on whose traces it represents.

    Read fresh each time the menu is drawn so it updates the moment option 3
    writes a new service into the config — the user always knows whose traces the
    demo and dashboard will show.
    """

    from argos import load_config

    service = load_config().service_name
    if not service:
        return "(unset)", "no service configured yet"
    if service == "research-assistant":
        return service, f"the bundled demo {console.DASH} no agent wrapped yet"
    return service, "your agent"


def _print_menu() -> None:
    service, note = _current_service()
    print()
    print(console.dim(f"Current service: ") + console.cyan(service)
          + console.dim(f"  ({note})"))
    print()
    print(f"  {console.bold('1')}) Connect AWS           - guides `aws configure`")
    print(f"  {console.bold('2')}) Start backend         - docker compose up, health-checked")
    print(f"  {console.bold('3')}) Instrument my agents  - add Argos to your own code (3 steps)")
    print(f"  {console.bold('4')}) Run the bundled demo  - the research-assistant example, to see traces flow")
    print(f"  {console.bold('5')}) Open dashboard        - Grafana (filter by service to pick whose traces)")
    print(f"  {console.bold('6')}) Settings              - view argos.config.yml")
    print(f"  {console.bold('0')}) Quit")


def run_menu() -> int:
    """Render the menu and dispatch choices until the user quits."""

    root = checks.repo_root()

    # Each entry: a zero-arg callable. Options needing the repo root close over it.
    actions: dict[str, Callable[[], None]] = {
        "1": connect_aws,
        "2": lambda: start_backend(root),
        "3": lambda: instrument_agents(root),
        "4": lambda: run_demo(root),
        "5": open_dashboard,
        "6": show_settings,
    }

    _banner()
    _ensure_config_present(root)
    while True:
        _print_menu()
        try:
            # lstrip the BOM too: piping input on Windows can prepend U+FEFF,
            # which .strip() leaves in place and would break the match.
            choice = input("\n" + console.cyan("Choose an option: ")).strip().lstrip(chr(0xFEFF)).lower()
        except EOFError:
            print()
            return 0

        if choice in ("0", "q", "quit", "exit"):
            print(console.dim("Bye."))
            return 0

        action = actions.get(choice)
        if action is None:
            console.line(console.MARK_WARN, "Unknown option", f"'{choice}'")
            continue

        try:
            action()
        except KeyboardInterrupt:
            print(console.dim("\n(interrupted - back to menu)"))
        console.pause()


if __name__ == "__main__":
    raise SystemExit(run_menu())
