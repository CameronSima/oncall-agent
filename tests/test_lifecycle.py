"""Lifecycle tests: drive the propose -> consent -> execute -> verify path
without hitting the LLM by constructing a Proposal directly.
"""
from __future__ import annotations

from pathlib import Path

from oncall_agent.agent.consent import AutoApproveLowRisk, PolicyConsent
from oncall_agent.agent.executor import Executor, Verifier
from oncall_agent.agent.lifecycle import AuditLog, Proposal, new_incident_id
from oncall_agent.incidents import run_scenario
from oncall_agent.incidents.scenarios import by_id


def _proposal_for(scenario_id: str, ctx, action_type: str, target: str, risk="low", conf=0.9) -> Proposal:
    return Proposal(
        incident_id=new_incident_id(),
        root_cause=f"synthetic for {scenario_id}",
        evidence=["e1"],
        proposed_remediation="synthetic",
        action_type=action_type,
        action_target=target,
        risk=risk,
        confidence=conf,
    )


def test_low_risk_revert_approves_executes_and_recovers(tmp_path: Path) -> None:
    s = by_id("bad_deploy_500")
    ctx = run_scenario(s)
    deploy_id = ctx.deploys.list_recent("orders", 0, 10**12)[0].id

    proposal = _proposal_for(s.id, ctx, "revert_deploy", deploy_id, risk="low", conf=0.95)
    broker = AutoApproveLowRisk()

    consent = broker.decide(proposal)
    assert consent.approved

    execution = Executor(ctx=ctx, scenario=s).run(proposal.action_type, proposal.action_target)
    assert execution.outcome == "ok"
    assert "reverted" in execution.detail

    verification = Verifier(ctx=ctx).run(execution)
    assert verification.recovered is True


def test_high_risk_rejected_under_low_risk_policy() -> None:
    s = by_id("bad_deploy_500")
    ctx = run_scenario(s)
    deploy_id = ctx.deploys.list_recent("orders", 0, 10**12)[0].id
    proposal = _proposal_for(s.id, ctx, "revert_deploy", deploy_id, risk="high", conf=0.9)

    consent = AutoApproveLowRisk().decide(proposal)
    assert not consent.approved
    assert "risk=high" in consent.reason


def test_unknown_deploy_id_yields_execute_error() -> None:
    s = by_id("bad_deploy_500")
    ctx = run_scenario(s)
    execution = Executor(ctx=ctx, scenario=s).run("revert_deploy", "dep_not_a_real_id")
    assert execution.outcome.startswith("error")
    assert "unknown_deploy" in execution.outcome


def test_escalation_action_skips_verification() -> None:
    s = by_id("dependency_timeout")
    ctx = run_scenario(s)
    execution = Executor(ctx=ctx, scenario=s).run("page_team", "payments")
    assert execution.outcome == "ok"
    verification = Verifier(ctx=ctx).run(execution)
    # Escalations are not verifiable by re-driving traffic.
    assert verification.recovered is None


def test_policy_consent_allowlist() -> None:
    s = by_id("memory_leak")
    ctx = run_scenario(s)
    deploy_id = ctx.deploys.list_recent("orders", 0, 10**12)[0].id

    proposal_allowed = _proposal_for(s.id, ctx, "revert_deploy", deploy_id, risk="low")
    proposal_blocked = _proposal_for(s.id, ctx, "restart_service", "orders", risk="low")

    policy = PolicyConsent(allowed={"revert_deploy": "low"})
    assert policy.decide(proposal_allowed).approved
    decision = policy.decide(proposal_blocked)
    assert not decision.approved
    assert "not in allowlist" in decision.reason


def test_audit_log_records_stages_in_order(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    log = AuditLog(path)
    log.write("intent", {"alert": "x"})
    log.write("propose", {"action_type": "noop"})
    log.write("consent", {"approved": True})
    log.write("audit_complete", {"ok": True})

    lines = [l for l in path.read_text().splitlines() if l]
    stages = [__import__("json").loads(l)["stage"] for l in lines]
    assert stages == ["intent", "propose", "consent", "audit_complete"]
