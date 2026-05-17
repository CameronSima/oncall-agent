"""Real Prometheus + Loki adapters.

Stubbed until we wire up a real cluster. The shape is here so the agent code
can stay identical when we swap backends.
"""
from __future__ import annotations

from .base import Alert, Deploy, LogLine


class PrometheusMetrics:
    def __init__(self, base_url: str) -> None:
        self.base_url = base_url

    def query(self, metric: str, since: float, until: float) -> list[tuple[float, float]]:
        # POST {base_url}/api/v1/query_range with metric, start=since, end=until, step=...
        raise NotImplementedError("Wire up httpx call to Prometheus query_range")

    def list_metrics(self, service: str | None = None) -> list[str]:
        # GET {base_url}/api/v1/label/__name__/values
        raise NotImplementedError


class LokiLogs:
    def __init__(self, base_url: str) -> None:
        self.base_url = base_url

    def query(self, contains, service, level, since, until, limit=100) -> list[LogLine]:
        # GET {base_url}/loki/api/v1/query_range with a LogQL expression.
        raise NotImplementedError
