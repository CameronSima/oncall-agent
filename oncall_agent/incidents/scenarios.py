"""The eval set. Each scenario is a single bug with a known root cause label.

The agent's diagnosis is scored against `expected_root_cause` (LLM-as-judge in
`evals/score.py`) and `expected_remediation`.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Scenario:
    id: str
    name: str
    severity: str  # "page" | "ticket"
    alert_name: str
    alert_description: str
    bug_flag: str  # attribute name on app.bugs.FLAGS
    plant_deploy: bool  # if True, plant a deploy ~10min before the alert
    expected_root_cause: str
    expected_remediation: str
    expected_action_type: str  # one of executor.ALLOWED_ACTIONS
    expected_action_target_kind: str  # "deploy_id" | "service" | "team" | ""


SCENARIOS: list[Scenario] = [
    Scenario(
        id="memory_leak",
        name="Orders service RSS climbing",
        severity="ticket",
        alert_name="HighMemoryUsage",
        alert_description="orders process RSS > 2GB and rising for 15m",
        bug_flag="memory_leak",
        plant_deploy=True,
        expected_root_cause=(
            "A recent deploy introduced a per-request memory allocation that is "
            "never released (memory leak in /order)."
        ),
        expected_remediation="Revert the most recent orders deploy.",
        expected_action_type="revert_deploy",
        expected_action_target_kind="deploy_id",
    ),
    Scenario(
        id="bad_deploy_500",
        name="5xx spike on /order",
        severity="page",
        alert_name="HighErrorRate",
        alert_description="orders 5xx rate > 5% for 5m",
        bug_flag="bad_deploy_500",
        plant_deploy=True,
        expected_root_cause=(
            "The most recent orders deploy introduced a NullPointerException on "
            "the checkout path for a subset of item_ids."
        ),
        expected_remediation="Revert the most recent orders deploy.",
        expected_action_type="revert_deploy",
        expected_action_target_kind="deploy_id",
    ),
    Scenario(
        id="slow_db_query",
        name="Latency p99 blown",
        severity="page",
        alert_name="HighLatency",
        alert_description="orders p99 > 2s for 10m",
        bug_flag="slow_db_query",
        plant_deploy=False,
        expected_root_cause=(
            "A database query on the checkout path is taking ~5s instead of ~20ms. "
            "No recent deploy — likely missing index or table bloat."
        ),
        expected_remediation=(
            "Engage the DB on-call. Check pg_stat_statements for the slow query; "
            "consider re-adding the index on orders.item_id."
        ),
        expected_action_type="engage_db_oncall",
        expected_action_target_kind="",
    ),
    Scenario(
        id="dependency_timeout",
        name="Payments dependency down",
        severity="page",
        alert_name="DependencyTimeout",
        alert_description="orders -> payments timeout rate > 50% for 5m",
        bug_flag="dependency_timeout",
        plant_deploy=False,
        expected_root_cause=(
            "The downstream payments service is timing out on every request from "
            "orders. The bug is in payments, not orders."
        ),
        expected_remediation=(
            "Page the payments on-call. Consider failing orders open to a queue "
            "while payments is down."
        ),
        expected_action_type="page_team",
        expected_action_target_kind="team",
    ),
]


def by_id(scenario_id: str) -> Scenario:
    for s in SCENARIOS:
        if s.id == scenario_id:
            return s
    raise KeyError(f"unknown scenario: {scenario_id}")
