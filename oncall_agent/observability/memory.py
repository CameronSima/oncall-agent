"""In-memory observability backends for fast eval iteration."""
from __future__ import annotations

import time
from collections import defaultdict

from .base import Alert, Deploy, LogLine


class InMemoryMetrics:
    def __init__(self) -> None:
        self._series: dict[str, list[tuple[float, float]]] = defaultdict(list)

    def record(self, metric: str, value: float, ts: float | None = None) -> None:
        self._series[metric].append((ts if ts is not None else time.time(), value))

    def query(self, metric: str, since: float, until: float) -> list[tuple[float, float]]:
        return [(t, v) for t, v in self._series.get(metric, []) if since <= t <= until]

    def list_metrics(self, service: str | None = None) -> list[str]:
        names = list(self._series.keys())
        if service:
            names = [n for n in names if n.startswith(f"{service}.") or f"{{service=\"{service}\"" in n]
        return sorted(names)


class InMemoryLogs:
    def __init__(self) -> None:
        self._lines: list[LogLine] = []

    def append(self, line: LogLine) -> None:
        self._lines.append(line)

    def query(
        self,
        contains: str | None,
        service: str | None,
        level: str | None,
        since: float,
        until: float,
        limit: int = 100,
    ) -> list[LogLine]:
        out: list[LogLine] = []
        for line in reversed(self._lines):
            if not (since <= line.ts <= until):
                continue
            if service and line.service != service:
                continue
            if level and line.level != level:
                continue
            if contains and contains.lower() not in line.message.lower():
                continue
            out.append(line)
            if len(out) >= limit:
                break
        return out


class InMemoryDeploys:
    def __init__(self) -> None:
        self._deploys: list[Deploy] = []
        self._diffs: dict[str, str] = {}

    def record(self, deploy: Deploy, diff: str) -> None:
        self._deploys.append(deploy)
        self._diffs[deploy.id] = diff

    def list_recent(self, service: str | None, since: float, until: float) -> list[Deploy]:
        return [
            d
            for d in self._deploys
            if since <= d.ts <= until and (service is None or d.service == service)
        ]

    def get_diff(self, deploy_id: str) -> str:
        return self._diffs.get(deploy_id, "")


class InMemoryAlerts:
    def __init__(self) -> None:
        self._alerts: dict[str, Alert] = {}

    def get(self, alert_id: str) -> Alert:
        return self._alerts[alert_id]

    def fire(self, alert: Alert) -> None:
        self._alerts[alert.id] = alert
