"""Tests for the single bot-wrapping config (sdk/argos/config.py) and its
wiring into init_tracing().

Two things matter here:
  1. the file is parsed correctly with defaults filling any gaps, and
  2. ``init_tracing()`` with NO arguments picks up service_name from the file —
     the Phase C adoption promise.
"""

import pytest

from argos import init_tracing, load_config, trace_step
from argos.config import ArgosConfig, find_config_file


EXAMPLE_YAML = """\
service_name: cfg-service
backend: localhost:29092
bedrock_model: anthropic.claude-3-haiku-20240307-v1:0
aws_region: eu-west-1
detection:
  loop_count: 7
  failure_count: 4
  cost_limit_usd: 2.50
"""


def _write_config(tmp_path, text):
    path = tmp_path / "argos.config.yml"
    path.write_text(text, encoding="utf-8")
    return path


# --- parsing --------------------------------------------------------------
def test_load_config_parses_all_fields(tmp_path):
    path = _write_config(tmp_path, EXAMPLE_YAML)
    cfg = load_config(path)

    assert cfg.service_name == "cfg-service"
    assert cfg.backend == "localhost:29092"
    assert cfg.has_backend
    assert cfg.bedrock_model == "anthropic.claude-3-haiku-20240307-v1:0"
    assert cfg.aws_region == "eu-west-1"
    assert cfg.detection.loop_count == 7
    assert cfg.detection.failure_count == 4
    assert cfg.detection.cost_limit_usd == 2.50
    assert cfg.source == path


def test_missing_file_returns_all_defaults(tmp_path):
    # An explicit path that doesn't exist → defaults, not an error.
    cfg = load_config(tmp_path / "nope.yml")
    assert cfg == ArgosConfig()
    assert cfg.service_name is None
    assert not cfg.has_backend
    assert cfg.detection.loop_count == 5


def test_partial_file_fills_defaults(tmp_path):
    path = _write_config(tmp_path, "service_name: only-name\n")
    cfg = load_config(path)
    assert cfg.service_name == "only-name"
    assert cfg.backend is None
    assert cfg.aws_region == "us-east-1"        # default
    assert cfg.detection.failure_count == 3     # default


def test_blank_backend_is_not_a_backend(tmp_path):
    path = _write_config(tmp_path, "service_name: s\nbackend: '   '\n")
    assert load_config(path).has_backend is False


def test_malformed_file_raises(tmp_path):
    path = _write_config(tmp_path, "- just\n- a\n- list\n")
    with pytest.raises(ValueError):
        load_config(path)


# --- discovery ------------------------------------------------------------
def test_env_var_points_at_file(tmp_path, monkeypatch):
    path = _write_config(tmp_path, EXAMPLE_YAML)
    monkeypatch.setenv("ARGOS_CONFIG", str(path))
    assert find_config_file() == path


# --- wiring into init_tracing() ------------------------------------------
def test_init_tracing_no_args_uses_config_service(tmp_path, monkeypatch):
    path = _write_config(tmp_path, "service_name: from-config\n")
    monkeypatch.setenv("ARGOS_CONFIG", str(path))

    spans = []
    # No service= passed: it must come from the file. Explicit sink so the test
    # never tries to build a real KafkaSink for the backend line.
    init_tracing(sink=spans.append)
    with trace_step(agent_name="a", step_type="tool_call", name="n"):
        pass

    assert spans and spans[0].service_name == "from-config"


def test_init_tracing_explicit_service_overrides_config(tmp_path, monkeypatch):
    path = _write_config(tmp_path, "service_name: from-config\n")
    monkeypatch.setenv("ARGOS_CONFIG", str(path))

    spans = []
    init_tracing(service="explicit-wins", sink=spans.append)
    with trace_step(agent_name="a", step_type="tool_call", name="n"):
        pass

    assert spans[0].service_name == "explicit-wins"


def test_init_tracing_no_service_anywhere_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("ARGOS_CONFIG", str(tmp_path / "absent.yml"))
    with pytest.raises(RuntimeError):
        init_tracing(sink=lambda s: None)
