"""Every scenario in the eval set should run through the scenario runner and
produce a context the tool surface can answer queries against.

These tests don't hit the LLM — they verify that scenarios are well-formed and
that the agent will be able to find the relevant evidence via tool calls.
"""
from __future__ import annotations

import json
import time

import pytest

from oncall_agent.agent.executor import ALLOWED_ACTIONS, Executor, Verifier
from oncall_agent.agent.memory import EpisodicMemory, Scratchpad
from oncall_agent.agent.slack import SlackChannel
from oncall_agent.agent.tools import ToolBox
from oncall_agent.incidents import SCENARIOS, run_scenario


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda s: s.id)
def test_scenario_runner_populates_evidence(scenario, tmp_path) -> None:
    """Each scenario must populate at least one metric AND either a log or a deploy."""
    ctx = run_scenario(scenario)
    metric_names = ctx.metrics.list_metrics()
    has_logs = (
        ctx.logs.query(contains=None, service=None, level=None, since=0, until=10**12, limit=1)
        != []
    )
    has_deploys = ctx.deploys.list_recent("orders", 0, 10**12) != []
    assert metric_names, f"{scenario.id}: no metrics recorded"
    assert has_logs or has_deploys, f"{scenario.id}: no logs and no deploys"
    assert ctx.alert.name == scenario.alert_name


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda s: s.id)
def test_scenario_expected_action_is_well_formed(scenario) -> None:
    assert scenario.expected_action_type in ALLOWED_ACTIONS
    if scenario.expected_action_type == "revert_deploy":
        assert scenario.expected_action_target_kind == "deploy_id"
    elif scenario.expected_action_type == "page_team":
        assert scenario.expected_action_target_kind == "team"
    elif scenario.expected_action_type in {"engage_db_oncall", "noop"}:
        assert scenario.expected_action_target_kind == ""


@pytest.mark.parametrize(
    "scenario_id",
    ["cpu_spike_regex", "n_plus_one_query", "canary_only_failures", "broken_feature_flag",
     "cache_disabled", "log_volume_explosion", "goroutine_leak"],
)
def test_revert_scenarios_plant_at_least_one_deploy(scenario_id) -> None:
    """Revert scenarios are unsolvable if no deploy is plantable. Catch that early."""
    from oncall_agent.incidents.scenarios import by_id

    ctx = run_scenario(by_id(scenario_id))
    deploys = ctx.deploys.list_recent("orders", 0, 10**12)
    assert deploys, f"{scenario_id} expects revert_deploy but planted no deploys"


@pytest.mark.parametrize(
    "scenario_id",
    ["dns_failure", "upstream_rate_limit", "ssl_cert_expired", "region_partial_outage",
     "retry_storm"],
)
def test_escalation_scenarios_have_log_evidence(scenario_id) -> None:
    """Escalation decisions hinge on log content — make sure agents can find the signal."""
    from oncall_agent.incidents.scenarios import by_id

    ctx = run_scenario(by_id(scenario_id))
    error_logs = ctx.logs.query(
        contains=None, service=None, level="ERROR", since=0, until=10**12, limit=100
    )
    assert error_logs, f"{scenario_id}: no ERROR logs to investigate"


def test_fixture_revert_records_execution_but_skips_verification() -> None:
    """Fixture scenarios with revert_deploy should execute cleanly but report
    recovered=None (we can't verify via replay)."""
    from oncall_agent.incidents.scenarios import by_id

    s = by_id("cpu_spike_regex")
    ctx = run_scenario(s)
    deploy_id = ctx.deploys.list_recent("orders", 0, 10**12)[0].id
    execution = Executor(ctx=ctx, scenario=s).run("revert_deploy", deploy_id)
    assert execution.outcome == "ok"
    assert "reverted" in execution.detail
    verification = Verifier(ctx=ctx, scenario=s).run(execution)
    assert verification.recovered is None
    assert "fixture-based" in verification.summary
