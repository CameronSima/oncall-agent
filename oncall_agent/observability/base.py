"""Protocols for observability backends.

The agent only ever sees these interfaces. Swap `InMemory*` for `PromLoki*` in
production without touching agent code.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass
class LogLine:
    ts: float
    level: str  # "INFO" | "WARN" | "ERROR"
    service: str
    message: str
    fields: dict = field(default_factory=dict)


@dataclass
class Deploy:
    id: str
    ts: float
    service: str
    git_sha: str
    pr_number: int | None
    pr_title: str
    author: str
    diff_summary: str  # e.g. "+42 -8 across 3 files"


@dataclass
class Alert:
    id: str
    ts: float
    name: str  # e.g. "HighErrorRate"
    service: str
    severity: str  # "page" | "ticket"
    description: str
    labels: dict[str, str] = field(default_factory=dict)


class MetricsStore(Protocol):
    def query(self, metric: str, since: float, until: float) -> list[tuple[float, float]]:
        """Return (timestamp, value) samples for `metric` in the window."""

    def list_metrics(self, service: str | None = None) -> list[str]:
        """Names of known metrics, optionally filtered by service."""


class LogStore(Protocol):
    def query(
        self,
        contains: str | None,
        service: str | None,
        level: str | None,
        since: float,
        until: float,
        limit: int = 100,
    ) -> list[LogLine]:
        """Return log lines matching the filters (most recent first)."""


class DeployStore(Protocol):
    def list_recent(self, service: str | None, since: float, until: float) -> list[Deploy]:
        ...

    def get_diff(self, deploy_id: str) -> str:
        """Return the full PR diff text for a deploy."""


class AlertStore(Protocol):
    def get(self, alert_id: str) -> Alert: ...
    def fire(self, alert: Alert) -> None: ...
