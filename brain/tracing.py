"""OpenTelemetry tracing (optional): see every chat turn, model call and tool call as a trace in
Jaeger, Grafana Tempo, SigNoz or any OTLP collector (all free and open source).

Switch on: pip install opentelemetry-sdk opentelemetry-exporter-otlp-proto-http
and set BRAIN_OTEL_ENDPOINT=http://localhost:4318 (the collector's OTLP/HTTP address).
Traces carry timings, model names, tool names and sizes, never the text of your chats or memories.
"""
import contextlib
import logging
import os

log = logging.getLogger("brain.tracing")
_tracer = None
_tried = False


def tracer():
    global _tracer, _tried
    if _tried:
        return _tracer
    _tried = True
    endpoint = os.environ.get("BRAIN_OTEL_ENDPOINT", "").rstrip("/")
    if not endpoint:
        return None
    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        provider = TracerProvider(resource=Resource.create({"service.name": os.environ.get("BRAIN_OTEL_SERVICE", "ai-brain")}))
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint + "/v1/traces")))
        trace.set_tracer_provider(provider)
        _tracer = trace.get_tracer("ai-brain")
        log.info("Tracing to %s", endpoint)
    except Exception as exc:
        log.warning("Tracing is off: %s (pip install opentelemetry-sdk opentelemetry-exporter-otlp-proto-http)", exc)
    return _tracer


def status() -> dict:
    return {"on": tracer() is not None, "endpoint": os.environ.get("BRAIN_OTEL_ENDPOINT", "")}


@contextlib.contextmanager
def span(name: str, **attrs):
    t = tracer()
    if t is None:
        yield None
        return
    with t.start_as_current_span(name) as sp:
        for k, v in attrs.items():
            if isinstance(v, (str, int, float, bool)):
                sp.set_attribute(f"brain.{k}", v)
        yield sp


def wrap(name: str, fn, attr=None):
    """Trace calls to fn (only when tracing is on). attr(args, kwargs) -> dict of safe attributes."""
    import functools

    @functools.wraps(fn)
    def inner(*a, **kw):
        if tracer() is None:
            return fn(*a, **kw)
        with span(name, **((attr(a, kw) if attr else {}) or {})):
            return fn(*a, **kw)
    return inner
