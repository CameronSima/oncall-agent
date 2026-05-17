"""Activates a scenario: flips the bug flag, simulates traffic into the toy
service so metrics + logs populate, optionally plants a recent deploy, and
fires the alert.

Returns an `IncidentContext` holding the wired-up stores + alert — this is what
the agent gets handed.
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
    bugs.reset()
    setattr(bugs.FLAGS, scenario.bug_flag, True)

    # Reset the app's metric/log sinks for a clean run.
    app_main.METRICS = InMemoryMetrics()
    app_main.LOGS = InMemoryLogs()
    app_main._LEAKED.clear()

    now = now if now is not None else time.time()
    deploys = InMemoryDeploys()

    if scenario.plant_deploy:
        deploy = Deploy(
            id=f"dep_{uuid.uuid4().hex[:8]}",
            ts=now - 600,
            service="orders",
            git_sha="abc123",
            pr_number=4711,
            pr_title=f"perf: tweak {scenario.bug_flag.replace('_', ' ')}",
            author="alice",
            diff_summary="+12 -3 across 2 files",
        )
        deploys.record(deploy, _DIFFS.get(scenario.id, "(diff unavailable)"))

    # Drive synthetic traffic so metrics + logs are present.
    client = TestClient(app_main.app)
    for i in range(40):
        try:
            client.post("/order", params={"item_id": i, "qty": 1})
        except Exception:
            pass

    alerts = InMemoryAlerts()
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
        alert=alert,
        metrics=app_main.METRICS,
        logs=app_main.LOGS,
        deploys=deploys,
        alerts=alerts,
    )
