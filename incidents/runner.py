"""Activates a scenario.

Two modes:
  - bug_flag: flips a real flag in `app.bugs`, drives synthetic traffic through
    the FastAPI orders service, real metrics + logs accumulate.
  - fixture: a function in `fixtures.py` writes metrics/logs/deploys directly
    into the in-memory stores. Used for incident shapes the toy app can't
    reproduce (DNS failures, region outages, n+1 patterns, etc.).

Either way the agent's tool surface is identical.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass

from fastapi.testclient import TestClient

from ..app import bugs, main as app_main
from ..observability import (
    Alert,
    Deploy,
    InMemoryAlerts,
    InMemoryDeploys,
    InMemoryLogs,
    InMemoryMetrics,
)
from .fixtures import FIXTURES
from .scenarios import Scenario


@dataclass
class IncidentContext:
    alert: Alert
    metrics: InMemoryMetrics
    logs: InMemoryLogs
    deploys: InMemoryDeploys
    alerts: InMemoryAlerts


_DIFFS = {
    "memory_leak": (
        "diff --git a/app/order.py b/app/order.py\n"
        "+ cache.append(request.body)  # keep request bodies for audit (PERF-1247)\n"
    ),
    "bad_deploy_500": (
        "diff --git a/app/checkout.py b/app/checkout.py\n"
        "- user = lookup_user(item_id)\n"
        "+ user = lookup_user(item_id) if item_id % 3 else None\n"
        "+ user.address  # crashes when None\n"
    ),
}


def run_scenario(scenario: Scenario, now: float | None = None) -> IncidentContext:
    now = now if now is not None else time.time()

    metrics = InMemoryMetrics()
    logs = InMemoryLogs()
    deploys = InMemoryDeploys()
    alerts = InMemoryAlerts()

    if scenario.fixture is not None:
        # Declarative path: stores get populated directly.
        bugs.reset()
        FIXTURES[scenario.fixture](metrics, logs, deploys, now)
    else:
        # Traffic-driven path through the FastAPI orders service.
        bugs.reset()
        if scenario.bug_flag:
            setattr(bugs.FLAGS, scenario.bug_flag, True)

        # Reset the app's sinks and bind them to our local stores so the agent
        # reads through the same instances the app writes to.
        app_main.METRICS = metrics
        app_main.LOGS = logs
        app_main._LEAKED.clear()

        if scenario.plant_deploy:
            deploy = Deploy(
                id=f"dep_{uuid.uuid4().hex[:8]}",
                ts=now - 600,
                service="orders",
                git_sha="abc123",
                pr_number=4711,
                pr_title=f"perf: tweak {(scenario.bug_flag or '').replace('_', ' ')}",
                author="alice",
                diff_summary="+12 -3 across 2 files",
            )
            deploys.record(deploy, _DIFFS.get(scenario.id, "(diff unavailable)"))

        client = TestClient(app_main.app)
        for i in range(40):
            try:
                client.post("/order", params={"item_id": i, "qty": 1})
            except Exception:
                pass

    alert = Alert(
        id=f"alert_{uuid.uuid4().hex[:8]}",
        ts=now,
        name=scenario.alert_name,
        service="orders",
        severity=scenario.severity,
        description=scenario.alert_description,
        labels={"service": "orders"},
    )
    alerts.fire(alert)

    return IncidentContext(
        alert=alert, metrics=metrics, logs=logs, deploys=deploys, alerts=alerts
    )
