"""The single bot-wrapping config — one non-secret YAML file, read by the SDK.

The adoption promise (README §6) is that wrapping someone's agents stays 2–3
lines. Phase C pushes that further: put your non-secret settings in one file
(``argos.config.yml``) and ``init_tracing()`` takes **no arguments** at all —
it reads ``service_name`` and the span ``backend`` straight from here.

    from argos import init_tracing, trace_step
    init_tracing()                       # service + sink come from argos.config.yml

What lives in the file (see ``argos.config.example.yml``):

* ``service_name``  — stamped on every span.
* ``backend``       — Kafka bootstrap address; blank → spans print to console.
* ``bedrock_model`` / ``aws_region`` — the model the demo bots call (non-secret).
* ``detection``     — loop / failure / cost thresholds for the Phase 4 rules.

**Secrets never live here.** AWS credentials come from the standard credential
chain (``aws configure`` / IAM role), never from this file — that's the whole
point of Phase C1.

Discovery order for the file (first hit wins):
  1. an explicit ``path`` passed to :func:`load_config`,
  2. the ``ARGOS_CONFIG`` environment variable,
  3. ``argos.config.yml`` found by walking up from the current directory.

If no file is found, :func:`load_config` returns an all-defaults config rather
than raising — so the SDK still works with zero configuration.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

CONFIG_FILENAME = "argos.config.yml"


@dataclass
class DetectionThresholds:
    """Mirror of the backend's tunable rule thresholds, sourced from the file.

    Kept here (rather than importing the backend) so the SDK stays standalone —
    the backend reads these same keys via ``DetectionConfig.from_config``.
    """

    loop_count: int = 5
    failure_count: int = 3
    cost_limit_usd: float = 1.00


@dataclass
class ArgosConfig:
    """Parsed view of ``argos.config.yml`` with sensible defaults for anything
    the file omits. ``source`` records which file it came from (None = defaults).
    """

    service_name: Optional[str] = None
    backend: Optional[str] = None          # Kafka bootstrap; None/"" → console sink
    bedrock_model: Optional[str] = None
    aws_region: str = "us-east-1"
    detection: DetectionThresholds = field(default_factory=DetectionThresholds)
    source: Optional[Path] = None

    @property
    def has_backend(self) -> bool:
        """True when a non-blank Kafka backend is configured."""

        return bool(self.backend and self.backend.strip())


def find_config_file(path: Optional[str | os.PathLike[str]] = None) -> Optional[Path]:
    """Locate the config file by the documented precedence; None if absent.

    1. explicit ``path``  2. ``ARGOS_CONFIG`` env var  3. walk up from cwd.
    """

    if path:
        p = Path(path).expanduser()
        return p if p.is_file() else None

    env_path = os.getenv("ARGOS_CONFIG")
    if env_path:
        p = Path(env_path).expanduser()
        return p if p.is_file() else None

    # Walk up from the current working directory looking for the file. This lets
    # a user run the demo from the repo root (or any subdir) and still be found.
    here = Path.cwd().resolve()
    for directory in (here, *here.parents):
        candidate = directory / CONFIG_FILENAME
        if candidate.is_file():
            return candidate
    return None


def load_config(path: Optional[str | os.PathLike[str]] = None) -> ArgosConfig:
    """Load and parse the config file, filling defaults for anything missing.

    Returns an all-defaults :class:`ArgosConfig` (``source=None``) when no file
    is found, so callers never have to special-case "no config". Raises only on a
    file that exists but is malformed — a silent misparse would be worse.
    """

    config_path = find_config_file(path)
    if config_path is None:
        return ArgosConfig()

    try:
        import yaml  # core dependency; imported here to keep `import argos` light
    except ImportError as exc:  # pragma: no cover - safety net; pyyaml is a dep
        raise ImportError(
            "Reading argos.config.yml needs PyYAML. Install the SDK normally:\n"
            "    pip install -e sdk"
        ) from exc

    with config_path.open("r", encoding="utf-8") as fh:
        raw: Any = yaml.safe_load(fh) or {}

    if not isinstance(raw, dict):
        raise ValueError(
            f"{config_path} must be a YAML mapping of settings, got {type(raw).__name__}."
        )

    det_raw = raw.get("detection") or {}
    if not isinstance(det_raw, dict):
        raise ValueError(f"{config_path}: 'detection' must be a mapping of thresholds.")
    det_defaults = DetectionThresholds()
    detection = DetectionThresholds(
        loop_count=int(det_raw.get("loop_count", det_defaults.loop_count)),
        failure_count=int(det_raw.get("failure_count", det_defaults.failure_count)),
        cost_limit_usd=float(det_raw.get("cost_limit_usd", det_defaults.cost_limit_usd)),
    )

    defaults = ArgosConfig()
    return ArgosConfig(
        service_name=raw.get("service_name") or defaults.service_name,
        backend=raw.get("backend") or defaults.backend,
        bedrock_model=raw.get("bedrock_model") or defaults.bedrock_model,
        aws_region=raw.get("aws_region") or defaults.aws_region,
        detection=detection,
        source=config_path,
    )
