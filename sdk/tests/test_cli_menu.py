"""Tests for the interactive menu's dispatch logic (Phase C4).

We drive the menu by monkeypatching ``input`` with a scripted list of answers
rather than piping real stdin — that's deterministic and avoids the host's
console-encoding quirks. The heavy options (aws configure, docker compose, the
demo) shell out to real tools, so they're covered by walking the menu by hand;
here we prove the *shell* routes correctly and the safe options behave.
"""

import builtins

from argos.cli import console, menu


def _scripted_input(responses):
    """Return a fake ``input`` that yields each response, then raises EOF."""

    it = iter(responses)

    def fake(prompt: str = "") -> str:
        try:
            return next(it)
        except StopIteration:
            raise EOFError
        # the menu treats EOF as "quit", mirroring a real Ctrl-D / closed pipe

    return fake


# --- quit paths -----------------------------------------------------------
def test_menu_quits_on_zero(monkeypatch, capsys):
    monkeypatch.setattr(builtins, "input", _scripted_input(["0"]))
    assert menu.run_menu() == 0
    assert "guided console" in capsys.readouterr().out


def test_menu_quits_on_eof(monkeypatch):
    monkeypatch.setattr(builtins, "input", _scripted_input([]))
    assert menu.run_menu() == 0


def test_menu_unknown_option_warns_then_continues(monkeypatch, capsys):
    # 'x' is unrecognized (warns, no pause), then '0' quits.
    monkeypatch.setattr(builtins, "input", _scripted_input(["x", "0"]))
    assert menu.run_menu() == 0
    assert "Unknown option" in capsys.readouterr().out


# --- a safe option routes correctly --------------------------------------
def test_menu_settings_shows_config(monkeypatch, capsys):
    # '6' -> settings, '' -> the pause Enter, '0' -> quit.
    monkeypatch.setattr(builtins, "input", _scripted_input(["6", "", "0"]))
    assert menu.run_menu() == 0
    out = capsys.readouterr().out
    assert "service_name" in out
    assert "Config source" in out


def test_open_dashboard_declined_does_not_launch_browser(monkeypatch):
    # Grafana port down + user declines -> must NOT open a browser.
    monkeypatch.setattr(menu.checks, "port_in_use", lambda *a, **k: False)
    monkeypatch.setattr(menu.console, "confirm", lambda *a, **k: False)
    opened = []
    monkeypatch.setattr(menu.webbrowser, "open", lambda url: opened.append(url))
    menu.open_dashboard()
    assert opened == []


# --- input helpers degrade safely on EOF ---------------------------------
def test_prompt_returns_default_on_eof(monkeypatch):
    monkeypatch.setattr(builtins, "input", _scripted_input([]))
    assert console.prompt("name", default="svc") == "svc"


def test_confirm_returns_default_on_eof(monkeypatch):
    monkeypatch.setattr(builtins, "input", _scripted_input([]))
    assert console.confirm("ok?", default=True) is True
    monkeypatch.setattr(builtins, "input", _scripted_input([]))
    assert console.confirm("ok?", default=False) is False


# --- background service management (consumer + detector) ------------------
def test_pid_alive_false_for_none_and_bogus():
    assert menu._pid_alive(None) is False
    # An absurd PID that won't exist on any normal machine.
    assert menu._pid_alive(2_000_000_000) is False


def test_ensure_consumer_skips_when_already_running(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(menu, "_already_running", lambda *a, **k: 4242)

    def _must_not_spawn(*a, **k):
        raise AssertionError("should not start a second service")

    monkeypatch.setattr(menu.subprocess, "Popen", _must_not_spawn)
    menu._ensure_consumer(tmp_path)
    out = capsys.readouterr().out
    assert "already running" in out
    assert "4242" in out


def test_ensure_detector_skips_when_already_running(monkeypatch, capsys, tmp_path):
    # The detector shares the same background-service machinery as the consumer.
    monkeypatch.setattr(menu, "_already_running", lambda *a, **k: 5151)

    def _must_not_spawn(*a, **k):
        raise AssertionError("should not start a second service")

    monkeypatch.setattr(menu.subprocess, "Popen", _must_not_spawn)
    menu._ensure_detector(tmp_path)
    out = capsys.readouterr().out
    assert "already running" in out
    assert "5151" in out


def test_read_pid_roundtrip(tmp_path):
    assert menu._read_pid(tmp_path, "consumer") is None    # nothing written yet
    menu._pidfile(tmp_path, "consumer").write_text("777", encoding="utf-8")
    assert menu._read_pid(tmp_path, "consumer") == 777
    # PID files are per-service: the detector's is independent of the consumer's.
    assert menu._read_pid(tmp_path, "detector") is None
