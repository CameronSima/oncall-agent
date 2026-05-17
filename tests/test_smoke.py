"""Smoke tests that don't hit the LLM.

Verifies the observability stack, scenario runner, and tool dispatch all work
end-to-end without any API calls.
"""
from __future__ import annotations

import json
from pathlib import Path

from oncall_agent.agent.memory import EpisodicMemory, PastIncident, Scratchpad
from oncall_agent.agent.slack import SlackChannel
from oncall_agent.agent.tools import TOOL_SCHEMAS, ToolBox
from oncall_agent.incidents import run_scenario
from oncall_agent.incidents.scenarios import by_id


def test_scenario_runner_populates_stores() -> None:
    ctx = run_scenario(by_id("bad_deploy_500"))
    assert ctx.alert.name == "HighErrorRate"
    assert ctx.metrics.list_metrics() != []
    error_logs = ctx.logs.query(
        contains=None, service="orders", level="ERROR", since=0, until=10**12, limit=50
    )
    assert any("checkout" in l.message.lower() for l in error_logs)
    deploys = ctx.deploys.list_recent("orders", 0, 10**12)
    assert len(deploys) == 1


def test_tool_dispatch_round_trip(tmp_path: Path) -> None:
    ctx = run_scenario(by_id("bad_deploy_500"))
    episodic = EpisodicMemory(tmp_path / "past.jsonl")
    episodic.add(
        PastIncident(
            id="past_1",
            alert_name="HighErrorRate",
            root_cause="bad deploy",
            remediation="revert",
            tags=["deploy", "5xx"],
        )
    )
    slack = SlackChannel(log_path=tmp_path / "slack.jsonl")
    tb = ToolBox(ctx=ctx, scratchpad=Scratchpad(), episodic=episodic, slack=slack)

    metrics = json.loads(tb.dispatch("list_metrics", {"service": "orders"}))
    assert "metrics" in metrics

    deploys = json.loads(tb.dispatch("list_recent_deploys", {"service": "orders"}))
    assert len(deploys["deploys"]) == 1
    deploy_id = deploys["deploys"][0]["id"]

    diff = json.loads(tb.dispatch("get_deploy_diff", {"deploy_id": deploy_id}))
    assert "checkout" in diff["diff"]

    past = json.loads(tb.dispatch("search_past_incidents", {"query": "HighErrorRate deploy"}))
    assert past["matches"][0]["id"] == "past_1"

    proposed = json.loads(
        tb.dispatch(
            "propose_action",
            {
                "root_cause": "bad deploy",
                "evidence": ["log line at checkout.py:84"],
                "proposed_remediation": "revert deploy",
                "action_type": "revert_deploy",
                "action_target": deploy_id,
                "risk": "low",
                "confidence": 0.9,
            },
        )
    )
    assert proposed["awaiting_human_approval"]
    assert tb.proposal is not None
    assert tb.proposal["action_type"] == "revert_deploy"


def test_tool_schemas_are_well_formed() -> None:
    for t in TOOL_SCHEMAS:
        assert "name" in t and "description" in t and "input_schema" in t
        assert t["input_schema"]["type"] == "object"
