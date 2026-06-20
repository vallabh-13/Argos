"""Argos detection — scan assembled traces for trouble and fire findings.

Public surface (pure, no Prometheus needed to import):

    from backend.detection import run_detection, DetectionConfig
    findings = run_detection(trace)            # trace: an AssembledTrace
    findings = run_detection(trace, DetectionConfig(loop_count=3))

Prometheus export lives in ``metrics`` (imported separately so the dependency is
only needed when you actually export).
"""

from .engine import RULES, run_detection
from .models import DetectionConfig, Finding, Severity

__all__ = ["run_detection", "RULES", "DetectionConfig", "Finding", "Severity"]
