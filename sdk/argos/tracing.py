"""The OpenTelemetry-based span emitter — Argos's public SDK surface.

This is what a user actually touches. The adoption promise (README §6) is that
instrumenting an agent step stays ~3 lines:

    from argos import init_tracing, trace_step

    init_tracing(service="research-assistant")

    with trace_step(agent_name="search", step_type="tool_call", name="web.search") as step:
        step.set_usage(model="...", tokens_in=120, tokens_out=80)
        step.set_cost(0.011)
        ...                         # the agent does its work

When the ``with`` block exits, Argos finalizes the step, **redacts secrets**, and
emits the span. In Phase 1 "emit" means *print structured JSON to the console*;
Phase 2 swaps that sink for Kafka without changing this API.

Why OpenTelemetry underneath instead of rolling our own? We get standard
``trace_id`` / ``span_id`` generation, accurate timing, and — crucially —
*context propagation*: nested ``trace_step`` calls automatically become
parent/child, which is the raw material the Phase 3 correlation engine needs.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Callable, Iterable, Iterator, Optional

from opentelemetry import trace as ot_trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.trace import (
    format_span_id,
    format_trace_id,
    Status as OTelStatus,
    StatusCode,
)

from .redaction import redact_mapping
from .sinks import console_sink
from .span import Span, Status, StepType, _utc_now

# --- module-level configuration, set by init_tracing() --------------------
_service_name: Optional[str] = None
_extra_denylist_keys: Optional[Iterable[str]] = None
_extra_patterns: Optional[Iterable[Any]] = None
_tracer: Optional[ot_trace.Tracer] = None

# The "sink" is where finished spans go. Defaults to printing JSON; pass a
# KafkaSink (or any callable) to init_tracing to change the destination.
_sink: Callable[[Span], None] = console_sink


def init_tracing(
    service: str,
    *,
    extra_denylist_keys: Optional[Iterable[str]] = None,
    extra_patterns: Optional[Iterable[Any]] = None,
    sink: Optional[Callable[[Span], None]] = None,
) -> None:
    """Set up tracing once, at application startup.

    ``service`` names the app being traced (e.g. ``"research-assistant"``) and
    is stamped on every span. ``extra_denylist_keys`` / ``extra_patterns`` let a
    user extend redaction for their own secret shapes. ``sink`` overrides where
    finished spans go (default: print JSON to stdout) — used by tests and, in
    Phase 2, by the Kafka producer.
    """

    global _service_name, _extra_denylist_keys, _extra_patterns, _tracer, _sink

    _service_name = service
    _extra_denylist_keys = extra_denylist_keys
    _extra_patterns = extra_patterns
    if sink is not None:
        _sink = sink

    # Install an OpenTelemetry provider if one isn't already set. OTel only
    # honors the first set_tracer_provider() per process, so we guard to stay
    # quiet on re-init (e.g. across tests).
    current = ot_trace.get_tracer_provider()
    if not isinstance(current, TracerProvider):
        ot_trace.set_tracer_provider(TracerProvider())
    _tracer = ot_trace.get_tracer("argos.sdk")


class StepRecorder:
    """Handle yielded by ``trace_step`` so the caller can attach data to a step.

    The user calls ``set_usage`` / ``set_cost`` / ``set_attribute`` on this while
    the step runs. Nothing is redacted or emitted until the ``with`` block exits
    and ``trace_step`` builds the final :class:`Span`.
    """

    def __init__(
        self,
        *,
        service_name: str,
        agent_name: str,
        step_type: StepType,
        name: str,
        start_attributes: Optional[dict[str, Any]] = None,
    ) -> None:
        self._service_name = service_name
        self._agent_name = agent_name
        self._step_type = step_type
        self._name = name
        self._attributes: dict[str, Any] = dict(start_attributes or {})
        self._model: Optional[str] = None
        self._tokens_in: int = 0
        self._tokens_out: int = 0
        self._cost_usd: float = 0.0
        self._error_message: Optional[str] = None
        # Captured now, when the step begins — NOT at _build() time, or the
        # span would record a start later than its end.
        self._start_time = _utc_now()
        # Filled in by trace_step once the OTel span exists.
        self._trace_id: str = ""
        self._span_id: str = ""
        self._parent_span_id: Optional[str] = None

    # -- user-facing setters (these are the ergonomic SDK calls) ----------
    def set_usage(
        self, *, model: str, tokens_in: int = 0, tokens_out: int = 0
    ) -> "StepRecorder":
        """Record the model and token counts for an LLM step (for cost)."""

        self._model = model
        self._tokens_in = tokens_in
        self._tokens_out = tokens_out
        return self

    def set_cost(self, cost_usd: float) -> "StepRecorder":
        """Record the dollar cost of this step."""

        self._cost_usd = cost_usd
        return self

    def set_attribute(self, key: str, value: Any) -> "StepRecorder":
        """Attach arbitrary metadata (e.g. MCP/A2A details). Redacted on emit."""

        self._attributes[key] = value
        return self

    def set_error(self, message: str) -> "StepRecorder":
        """Mark this step as failed with a reason."""

        self._error_message = message
        return self

    # -- internal: build the final, redacted Span -------------------------
    def _bind(self, trace_id: str, span_id: str, parent_span_id: Optional[str]) -> None:
        self._trace_id = trace_id
        self._span_id = span_id
        self._parent_span_id = parent_span_id

    def _build(self) -> Span:
        """Redact attributes and assemble the immutable Span to emit.

        Redaction happens HERE — before the span is handed to the sink — so
        secrets never leave the user's machine. ``redacted=True`` records that
        this scrubbing ran.
        """

        clean_attributes = redact_mapping(
            self._attributes,
            extra_denylist_keys=_extra_denylist_keys,
            extra_patterns=_extra_patterns,
        )
        status = Status.ERROR if self._error_message else Status.OK
        return Span(
            service_name=self._service_name,
            agent_name=self._agent_name,
            step_type=self._step_type,
            name=self._name,
            trace_id=self._trace_id,
            span_id=self._span_id,
            parent_span_id=self._parent_span_id,
            start_time=self._start_time,
            end_time=_utc_now(),
            status=status,
            error_message=self._error_message,
            model=self._model,
            tokens_in=self._tokens_in,
            tokens_out=self._tokens_out,
            cost_usd=self._cost_usd,
            attributes=clean_attributes,
            redacted=True,
        )


@contextmanager
def trace_step(
    *,
    agent_name: str,
    step_type: StepType | str,
    name: str,
    attributes: Optional[dict[str, Any]] = None,
) -> Iterator[StepRecorder]:
    """Trace one agent step. Use as a context manager.

    Opens an OpenTelemetry span (giving us real ``trace_id`` / ``span_id`` and
    automatic parent/child nesting), yields a :class:`StepRecorder` to attach
    data, and on exit redacts + emits the finished :class:`Span`. If the body
    raises, the step is recorded as an error and the exception re-raised.
    """

    if _service_name is None or _tracer is None:
        raise RuntimeError("init_tracing(...) must be called before trace_step(...)")

    # A plain string like "tool_call" is fine; coerce to the enum (fails loudly
    # on a typo) so callers don't have to import StepType.
    step_type = step_type if isinstance(step_type, StepType) else StepType(step_type)

    # Whatever span is currently active becomes our parent (None at the root).
    parent_ctx = ot_trace.get_current_span().get_span_context()
    parent_span_id = (
        format_span_id(parent_ctx.span_id) if parent_ctx.is_valid else None
    )

    recorder = StepRecorder(
        service_name=_service_name,
        agent_name=agent_name,
        step_type=step_type,
        name=name,
        start_attributes=attributes,
    )

    with _tracer.start_as_current_span(name) as otel_span:
        ctx = otel_span.get_span_context()
        recorder._bind(
            format_trace_id(ctx.trace_id),
            format_span_id(ctx.span_id),
            parent_span_id,
        )
        try:
            yield recorder
        except Exception as exc:  # noqa: BLE001 - we re-raise after recording
            recorder.set_error(str(exc))
            otel_span.set_status(OTelStatus(StatusCode.ERROR, str(exc)))
            span = recorder._build()
            _mirror_to_otel(otel_span, span)
            _sink(span)
            raise
        else:
            span = recorder._build()
            _mirror_to_otel(otel_span, span)
            _sink(span)


def _mirror_to_otel(otel_span: ot_trace.Span, span: Span) -> None:
    """Copy our (already-redacted) fields onto the OTel span as attributes.

    Phase 1 prints spans itself, so this is mostly forward-looking: when Phase 2
    wires an OTel exporter, the redacted data is already on the OTel span. We
    only ever set the *scrubbed* values here — never raw secrets.
    """

    otel_span.set_attribute("argos.agent_name", span.agent_name)
    otel_span.set_attribute("argos.step_type", span.step_type.value)
    if span.model:
        otel_span.set_attribute("argos.model", span.model)
    otel_span.set_attribute("argos.tokens_in", span.tokens_in)
    otel_span.set_attribute("argos.tokens_out", span.tokens_out)
    otel_span.set_attribute("argos.cost_usd", span.cost_usd)
    for key, value in span.attributes.items():
        # OTel only accepts primitive attribute types; stringify the rest.
        if isinstance(value, (str, bool, int, float)):
            otel_span.set_attribute(f"argos.attr.{key}", value)
        else:
            otel_span.set_attribute(f"argos.attr.{key}", str(value))
