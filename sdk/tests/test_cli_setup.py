"""Tests for the onboarding CLI's pure, deterministic pieces (Phase C3).

We deliberately don't shell out to Docker/AWS here — those depend on the host
and are covered by running `python -m argos setup` by hand. What we *can* test
in isolation: the safe config-copy action, the port probe, and the always-true
Python check.
"""

import socket

from argos.cli import checks, setup
from argos.cli.checks import Status


def test_check_python_is_ok_on_supported_runtime():
    # The test suite itself runs on >= 3.10, so this must report OK.
    assert checks.check_python().status is Status.OK


def test_port_in_use_false_for_free_port():
    # Bind to grab a free port, close it, then probe — nothing should listen.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    assert checks.port_in_use(port) is False


def test_port_in_use_true_for_listening_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as srv:
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        port = srv.getsockname()[1]
        assert checks.port_in_use(port) is True


def test_ensure_config_creates_then_is_idempotent(tmp_path):
    (tmp_path / "argos.config.example.yml").write_text("service_name: x\n", encoding="utf-8")
    real = tmp_path / "argos.config.yml"

    # First call creates it from the example.
    r1 = setup._ensure_config(tmp_path)
    assert r1.status is Status.OK
    assert real.is_file()
    assert "created" in r1.detail

    # Second call must NOT clobber — it reports the existing file.
    real.write_text("service_name: edited-by-user\n", encoding="utf-8")
    r2 = setup._ensure_config(tmp_path)
    assert r2.status is Status.OK
    assert "already present" in r2.detail
    assert real.read_text(encoding="utf-8") == "service_name: edited-by-user\n"


def test_ensure_config_warns_without_example(tmp_path):
    # No example file present → warn, don't crash.
    assert setup._ensure_config(tmp_path).status is Status.WARN
