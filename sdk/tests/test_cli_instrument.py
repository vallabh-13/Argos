"""Tests for the 'Instrument my agents' flow (Phase C5).

The live end-to-end check (spans actually detected in ClickHouse) is the manual
acceptance test. Here we lock down the deterministic parts: the config writer,
the three-step display, and the verify step's graceful guards.
"""

from argos.cli import checks, console, instrument


# --- config writer --------------------------------------------------------
class _Det:
    loop_count = 5
    failure_count = 3
    cost_limit_usd = 1.0


class _Cfg:
    bedrock_model = "anthropic.claude-3-haiku-20240307-v1:0"
    aws_region = "us-east-1"
    detection = _Det()


def test_write_config_with_backend(tmp_path):
    path = tmp_path / "argos.config.yml"
    instrument._write_config(path, "svc-a", "localhost:29092", _Cfg())
    text = path.read_text(encoding="utf-8")
    assert "service_name: svc-a" in text
    assert "backend: localhost:29092" in text
    assert "NON-SECRET" in text                      # the no-secrets promise stays
    assert "loop_count: 5" in text                   # carried over, not dropped


def test_write_config_console_backend_is_commented(tmp_path):
    path = tmp_path / "argos.config.yml"
    instrument._write_config(path, "svc-b", None, _Cfg())
    text = path.read_text(encoding="utf-8")
    assert "service_name: svc-b" in text
    # No active backend line; it's commented so spans default to the console.
    assert "\nbackend:" not in text
    assert "# backend:" in text


# --- three-step display ---------------------------------------------------
def test_show_steps_renders_all_three(capsys):
    instrument._show_steps("my-agents", "localhost:29092")
    out = capsys.readouterr().out
    assert "STEP 1 of 3" in out and "STEP 2 of 3" in out and "STEP 3 of 3" in out
    assert "from argos import init_tracing, trace_step" in out
    assert "init_tracing()" in out
    # The before/after framing is now spelled out as plain labels.
    assert "Your code now" in out and "Your code with Argos" in out
    assert "trace_step(" in out
    # Cost tracking is an explicitly-optional add-on, not in the basic wrap.
    assert "optional" in out.lower() and "set_usage(" in out


def test_show_steps_console_backend_mentions_console(capsys):
    instrument._show_steps("my-agents", None)
    out = capsys.readouterr().out
    assert "console" in out.lower()


# --- verify guards --------------------------------------------------------
def test_verify_backend_down_warns_and_returns(monkeypatch, capsys):
    # ClickHouse port closed -> must warn and return, never raise.
    monkeypatch.setattr(checks, "port_in_use", lambda *a, **k: False)
    instrument._verify("svc")
    assert "not reachable" in capsys.readouterr().out.lower()


def test_verify_missing_driver_is_handled(monkeypatch, capsys):
    # Port "open" but the clickhouse-connect import fails -> graceful message.
    monkeypatch.setattr(checks, "port_in_use", lambda *a, **k: True)

    def _boom():
        raise ImportError("no clickhouse_connect")

    monkeypatch.setattr(instrument, "_clickhouse_client", _boom)
    instrument._verify("svc")
    assert "clickhouse-connect" in capsys.readouterr().out


def test_troubleshoot_lists_service_name(capsys):
    instrument._troubleshoot("svc-xyz")
    out = capsys.readouterr().out
    assert "svc-xyz" in out
    assert "consumer" in out.lower()
