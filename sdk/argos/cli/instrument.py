"""Menu option 3 — the guided "Instrument my agents" flow (Phase C5).

This is the make-or-break onboarding moment: turning someone's existing
multi-agent app into one that emits Argos traces. The whole design goal is that
it's *one code change*, and that the tool proves it worked rather than leaving
the user guessing.

The flow has four beats:

  (a) SHOW   - explain it's a single, additive code change (nothing rewritten).
  (b) ASK    - collect the service name + span backend, and offer to write them
               into argos.config.yml so init_tracing() needs no arguments.
  (c) PLACE  - print three clearly-labeled copy-paste blocks (imports / one-time
               init / a BEFORE->AFTER wrap), each saying where it goes and why.
  (d) VERIFY - watch ClickHouse for spans carrying their service name and report
               success with a count, or honest troubleshooting if none arrive.

Honesty is a feature here: we show a *generic template* with the user's values
filled in. We never claim to auto-edit their source files, because guessing at
someone's code structure and rewriting it is exactly the kind of surprising,
breakable magic that erodes trust.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Optional

from . import checks, console

# The watch loop's patience and cadence. 90s is plenty for a person to switch to
# another terminal and kick off a run; 3s polling keeps the DB load trivial.
_WATCH_TIMEOUT_S = 90.0
_WATCH_INTERVAL_S = 3.0


def instrument_agents(root: Path) -> None:
    """Run the full show -> place -> verify flow for menu option 3."""

    console.heading("3) Instrument my agents")
    _explain()

    service, backend = _collect_settings(root)

    _show_steps(service, backend)

    if console.confirm("\nReady to verify that your spans are arriving?", default=True):
        _verify(service)
    else:
        print(console.dim("Skipped verification. Re-run option 3 any time to check."))


# --- (a) SHOW -------------------------------------------------------------
def _explain() -> None:
    print("Add Argos to your OWN agent in 3 small steps. We don't edit your files -")
    print("you paste 3 snippets in, run your agent, and we confirm your traces arrived.\n")


# --- (b) ASK --------------------------------------------------------------
def _collect_settings(root: Path) -> tuple[str, Optional[str]]:
    """Ask for service name + backend; optionally persist them to the config.

    Returns the (service_name, backend) the rest of the flow should show. We read
    current values from argos.config.yml as defaults so re-running is friendly.
    """

    from argos import load_config

    cfg = load_config()
    default_service = cfg.service_name or "my-agents"
    default_backend = cfg.backend or "localhost:29092"

    print(console.bold("Settings"))
    service = console.prompt("Service name (the label your traces show under in Grafana)",
                             default=default_service).strip()
    backend = console.prompt(
        "Span backend - Kafka address, or 'console' to just print",
        default=default_backend,
    ).strip()
    backend_value: Optional[str] = None if backend.lower() == "console" else backend

    # Offer to write the config so init_tracing() can be called with no arguments.
    config_path = root / "argos.config.yml"
    if console.confirm(f"\nWrite these into {config_path.name} (so init_tracing() needs no args)?",
                       default=True):
        if config_path.is_file():
            overwrite = console.confirm(
                f"{config_path.name} already exists - overwrite it?", default=False
            )
            if not overwrite:
                print(console.dim("Kept your existing config. Showing steps with the values above."))
                return service, backend_value
        _write_config(config_path, service, backend_value, cfg)
        console.line(console.MARK_OK, "Wrote config", str(config_path))

    return service, backend_value


def _write_config(path: Path, service: str, backend: Optional[str], cfg) -> None:
    """Write a minimal, comment-rich argos.config.yml with the user's values.

    We keep the "no secrets here" note front and center (Phase C1/C2 promise) and
    carry over the model/region/thresholds the loaded config already had, so
    writing this file never silently drops earlier settings.
    """

    backend_line = (
        f"backend: {backend}" if backend else "# backend:            # unset -> spans print to the console"
    )
    det = cfg.detection
    text = f"""\
# Argos bot-wrapping config. NON-SECRET settings only.
# AWS credentials come from the standard chain (`aws configure` / IAM role) - never here.

service_name: {service}
{backend_line}

bedrock_model: {cfg.bedrock_model or "anthropic.claude-haiku-4-5-20251001-v1:0"}
aws_region: {cfg.aws_region}

detection:
  loop_count: {det.loop_count}
  failure_count: {det.failure_count}
  cost_limit_usd: {det.cost_limit_usd}
"""
    path.write_text(text, encoding="utf-8")


# --- (c) PLACE ------------------------------------------------------------
def _show_steps(service: str, backend: Optional[str]) -> None:
    """Print the three copy-paste blocks, each with where-it-goes + why.

    Every snippet is complete and runnable on its own — broken-looking code in
    the instructions would undermine the whole "this is easy and safe" pitch.
    """

    backend_note = (
        f"loads your settings (service '{service}', backend '{backend}')"
        if backend is not None
        else f"loads your settings (service '{service}'); spans print to the console"
    )

    console.heading("The one code change - 3 steps")

    # Step 1 — imports.
    print(console.bold("STEP 1 of 3 - Add the imports"))
    print(console.dim("  WHERE: at the top of your agent's main file, with your other imports."))
    print(console.dim("  WHY:   makes Argos's two helpers available."))
    _block("from argos import init_tracing, trace_step")

    # Step 2 — one-time init.
    print(console.bold("STEP 2 of 3 - Turn tracing on once, at startup"))
    print(console.dim("  WHERE: right after the imports, before your agent does any work."))
    print(console.dim(f"  WHY:   {backend_note}."))
    _block("init_tracing()")

    # Step 3 — wrap a step. Keep the first thing they read the simplest version
    # that works; the optional cost line lives in its own note below.
    print(console.bold("STEP 3 of 3 - Wrap the steps you want to see"))
    print(console.dim("  WHERE: around one agent action - an LLM call, a tool call, a handoff."))
    print(console.dim("  WHY:   each wrapped block becomes one step (span) in your trace.\n"))

    print(console.dim(_label_rule("Your code now")))
    _block(
        "def run_search(query):\n"
        "    result = call_my_tool(query)\n"
        "    return result"
    )
    print(console.dim(_label_rule("Your code with Argos")))
    _block(
        "def run_search(query):\n"
        "    with trace_step(agent_name=\"search\",\n"
        "                    step_type=\"tool_call\",\n"
        "                    name=\"call_my_tool\") as step:\n"
        "        result = call_my_tool(query)\n"
        "        return result"
    )
    print(console.dim(f"  step_type is one of: llm_call {console.DOT} tool_call "
                      f"{console.DOT} a2a_handoff {console.DOT} decision"))
    print(console.dim("  You can wrap just ONE call to start - even a single span shows up in Grafana.\n"))

    # Optional extra, clearly fenced off so the simplest version reads first.
    print(console.bold("Want cost tracking too? (optional)"))
    print(console.dim("  Inside the `with` block, hand Argos the model + token counts:"))
    _block(
        "def run_search(query):\n"
        "    with trace_step(agent_name=\"search\",\n"
        "                    step_type=\"llm_call\",\n"
        "                    name=\"call_my_tool\") as step:\n"
        "        result = call_my_tool(query)\n"
        "        step.set_usage(model=\"my-model\", tokens_in=120, tokens_out=80)\n"
        "        return result"
    )

    # One clear closing instruction.
    print(console.bold("What to do now"))
    print("  1. Add the snippets above to your agent.")
    print("  2. Run your agent the way you normally do.")
    print("  3. Come back here and press Enter - we'll watch for your traces.")


def _label_rule(label: str, width: int = 30) -> str:
    """A '── label ─────' divider, using ASCII '-' when the console isn't UTF-8."""

    bar = console.RULE
    pad = max(2, width - len(label))
    return f"  {bar * 2} {label} {bar * pad}"


def _block(code: str) -> None:
    """Print a copy-paste code block, lightly set off from prose."""

    print()
    for ln in code.splitlines():
        print("    " + console.cyan(ln))
    print()


# --- (d) VERIFY -----------------------------------------------------------
def _verify(service: str) -> None:
    """Watch ClickHouse for spans from ``service`` and report the outcome."""

    console.heading("Verifying - watching for your spans")

    if not checks.port_in_use(8123):
        console.line(console.MARK_WARN, "Backend not reachable",
                     "ClickHouse :8123 isn't up - start it with menu option 2 first.")
        return

    try:
        client = _clickhouse_client()
    except ImportError:
        console.line(console.MARK_WARN, "Can't verify automatically",
                     "the 'clickhouse-connect' package isn't installed")
        print("       " + console.dim(console.ARROW + " pip install clickhouse-connect, "
                                       "then re-run option 3."))
        return
    except Exception as exc:  # noqa: BLE001 - connection refused, auth, etc.
        console.line(console.MARK_WARN, "Couldn't connect to ClickHouse", str(exc))
        return

    # Anchor on the SERVER clock so host/container time skew can't hide new rows.
    try:
        since = client.query("SELECT now()").result_rows[0][0]
    except Exception as exc:  # noqa: BLE001
        console.line(console.MARK_WARN, "ClickHouse query failed", str(exc))
        return

    print(f"Run your agent now so it emits spans for service '{console.cyan(service)}'.")
    print(console.dim("(The ingest consumer, started by option 2, drains them into ClickHouse.)"))
    console.prompt("Press Enter once your run has started - then we'll watch", default="")

    deadline = time.time() + _WATCH_TIMEOUT_S
    found = 0
    traces = 0
    while time.time() < deadline:
        try:
            rows = client.query(
                "SELECT count(), uniqExact(trace_id) FROM argos.spans "
                "WHERE service_name = {svc:String} AND ingested_at >= {since:DateTime}",
                parameters={"svc": service, "since": since},
            ).result_rows
            found, traces = int(rows[0][0]), int(rows[0][1])
        except Exception as exc:  # noqa: BLE001
            console.line(console.MARK_WARN, "Query error while watching", str(exc))
            return

        if found > 0:
            whose = ("the bundled demo's traces" if service == "research-assistant"
                     else "your own agent's traces")
            print()
            print(console.green(f"{console.CHECK} Success - {found} span(s) across {traces} "
                                f"trace(s) tagged service '{service}' reached ClickHouse."))
            print(console.dim(f"   That's {whose}. Open the dashboard (option 5) and "
                              f"filter service = '{service}'."))
            return

        remaining = int(deadline - time.time())
        print(console.dim(f"   watching... no spans yet ({remaining}s left)"))
        time.sleep(_WATCH_INTERVAL_S)

    _troubleshoot(service)


def _troubleshoot(service: str) -> None:
    print()
    console.line(console.MARK_WARN, "No spans arrived",
                 f"nothing for service '{service}' in {int(_WATCH_TIMEOUT_S)}s")
    print(console.dim("Checklist:"))
    for tip in (
        f"service name matches exactly - argos.config.yml says '{service}'?",
        "init_tracing() runs BEFORE your wrapped steps (not after)?",
        "the backend is up (menu option 2) and the address in the config is right?",
        "the ingest consumer is running - menu option 2 starts it; check .argos/consumer.log",
        "your run actually reached a wrapped trace_step block?",
    ):
        print("   " + console.dim("- " + tip))


def _clickhouse_client():
    """Open a ClickHouse client using the same env defaults as the backend.

    Inlined (rather than importing backend.storage) to keep the SDK's CLI from
    depending on the backend package - the defaults mirror docker-compose.yml.
    """

    import clickhouse_connect

    return clickhouse_connect.get_client(
        host=os.getenv("CLICKHOUSE_HOST", "localhost"),
        port=int(os.getenv("CLICKHOUSE_PORT", "8123")),
        database=os.getenv("CLICKHOUSE_DB", "argos"),
        username=os.getenv("CLICKHOUSE_USER", "argos"),
        password=os.getenv("CLICKHOUSE_PASSWORD", "argos"),
    )
