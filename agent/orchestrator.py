"""Layer 5: orchestration.

The agent loop (architectural):

    plan         -> ask the planner model for an investigation plan
    investigate  -> run the tool-use loop with the worker model, budget-capped
    critique     -> ask the planner model to poke holes in the hypothesis

After the agent calls `propose_action`, the orchestrator drives the action
LIFECYCLE without the LLM in the loop:

    propose  ->  consent  ->  execute  ->  verify  ->  audit_complete

Consent is the security boundary — the agent cannot bypass the broker.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from rich.console import Console

from ..incidents.runner import IncidentContext
from ..incidents.scenarios import Scenario
from .consent import AutoApproveLowRisk, ConsentBroker
from .executor import Executor, Verifier
from .lifecycle import (
    AuditLog,
    Consent,
    Execution,
    Intent,
    Proposal,
    Verification,
    new_incident_id,
)
from .memory import EpisodicMemory, Scratchpad
from .model import LLM
from .prompts import SYSTEM_PROMPT, alert_message
from .slack import SlackChannel
from .tools import TOOL_SCHEMAS, ToolBox


@dataclass
class Investigation:
    incident_id: str
    plan: str
    proposal: dict | None
    steps: int
    tool_calls: list[dict]
    slack_posts: list[dict]
    usage: dict
    critique: str | None = None
    consent: Consent | None = None
    execution: Execution | None = None
    verification: Verification | None = None
    audit_log_path: Path | None = None
    transcript: list[dict] = field(default_factory=list)


class Orchestrator:
    def __init__(
        self,
        llm: LLM | None = None,
        episodic_path: Path | None = None,
        slack_log_path: Path | None = None,
        audit_dir: Path | None = None,
        consent_broker: ConsentBroker | None = None,
        max_steps: int | None = None,
        console: Console | None = None,
    ) -> None:
        self.llm = llm or LLM()
        self.episodic = EpisodicMemory(
            episodic_path or Path("oncall_agent_data/past_incidents.jsonl")
        )
        self.slack_log_path = slack_log_path or Path("oncall_agent_data/slack.jsonl")
        self.audit_dir = audit_dir or Path("oncall_agent_data/audit")
        self.consent_broker = consent_broker or AutoApproveLowRisk()
        self.max_steps = max_steps or int(os.getenv("ONCALL_MAX_STEPS", "12"))
        self.console = console or Console()

    def run(self, ctx: IncidentContext, scenario: Scenario | None = None) -> Investigation:
        scratchpad = Scratchpad()
        slack = SlackChannel(self.slack_log_path, console=self.console)
        toolbox = ToolBox(ctx=ctx, scratchpad=scratchpad, episodic=self.episodic, slack=slack)

        incident_id = new_incident_id()
        audit = AuditLog(self.audit_dir / f"{incident_id}.jsonl")
        intent = Intent(
            incident_id=incident_id,
            alert_id=ctx.alert.id,
            alert_name=ctx.alert.name,
            service=ctx.alert.service,
            severity=ctx.alert.severity,
            description=ctx.alert.description,
        )
        audit.write("intent", intent)

        plan = self._plan(ctx)
        self.console.rule("[bold magenta]plan")
        self.console.print(plan)

        proposal_dict, steps, transcript = self._investigate(ctx, plan, toolbox)
        critique = self._critique(ctx, scratchpad.render(), proposal_dict) if proposal_dict else None

        consent: Consent | None = None
        execution: Execution | None = None
        verification: Verification | None = None

        if proposal_dict is not None:
            proposal = Proposal(
                incident_id=incident_id,
                root_cause=proposal_dict["root_cause"],
                evidence=proposal_dict["evidence"],
                proposed_remediation=proposal_dict["proposed_remediation"],
                action_type=proposal_dict["action_type"],
                action_target=proposal_dict.get("action_target", ""),
                risk=proposal_dict["risk"],
                confidence=float(proposal_dict["confidence"]),
            )
            audit.write("propose", proposal)

            consent = self.consent_broker.decide(proposal)
            audit.write("consent", consent)
            slack.post(
                f"CONSENT: {'approved' if consent.approved else 'rejected'} "
                f"by {consent.approver} — {consent.reason}"
            )

            if consent.approved and scenario is not None:
                execution = Executor(ctx=ctx, scenario=scenario).run(
                    proposal.action_type, proposal.action_target
                )
                execution.incident_id = incident_id
                audit.write("execute", execution)
                slack.post(f"EXECUTED: {execution.outcome} — {execution.detail}")

                verification = Verifier(ctx=ctx, scenario=scenario).run(execution)
                verification.incident_id = incident_id
                audit.write("verify", verification)
                slack.post(
                    f"VERIFY: recovered={verification.recovered} — {verification.summary}"
                )

        audit.write(
            "audit_complete",
            {
                "incident_id": incident_id,
                "proposed": proposal_dict is not None,
                "consented": bool(consent and consent.approved),
                "executed": execution is not None,
                "recovered": verification.recovered if verification else None,
            },
        )

        return Investigation(
            incident_id=incident_id,
            plan=plan,
            proposal=proposal_dict,
            steps=steps,
            tool_calls=[o.__dict__ for o in scratchpad.observations],
            slack_posts=slack.posts,
            usage=dict(self.llm.usage),
            critique=critique,
            consent=consent,
            execution=execution,
            verification=verification,
            audit_log_path=audit.path,
            transcript=transcript,
        )

    # --- stages ---

    def _plan(self, ctx: IncidentContext) -> str:
        resp = self.llm.call(
            role="planner",
            system=SYSTEM_PROMPT,
            messages=[
                {
                    "role": "user",
                    "content": (
                        f"You have just been paged.\n\n{alert_message(ctx.alert)}\n\n"
                        "Before you investigate, write a short numbered plan "
                        "(3-5 bullets) of what you'll check and in what order. "
                        "Do not call tools yet."
                    ),
                }
            ],
        )
        return "".join(b.text for b in resp.content if b.type == "text").strip()

    def _investigate(
        self, ctx: IncidentContext, plan: str, toolbox: ToolBox
    ) -> tuple[dict | None, int, list[dict]]:
        messages: list[dict] = [
            {
                "role": "user",
                "content": (
                    f"{alert_message(ctx.alert)}\n\n"
                    f"Your plan:\n{plan}\n\n"
                    "Now investigate. Call tools as needed. When you have a "
                    "diagnosis, call `propose_action` exactly once and stop."
                ),
            }
        ]

        steps = 0
        while steps < self.max_steps:
            resp = self.llm.call(
                role="worker",
                system=SYSTEM_PROMPT,
                messages=messages,
                tools=TOOL_SCHEMAS,
            )
            steps += 1

            messages.append({"role": "assistant", "content": resp.content})

            tool_uses = [b for b in resp.content if b.type == "tool_use"]
            if not tool_uses:
                if toolbox.proposal is None:
                    messages.append(
                        {
                            "role": "user",
                            "content": (
                                "You ended your turn without calling a tool. If you "
                                "have your diagnosis, call `propose_action`. Otherwise "
                                "continue investigating."
                            ),
                        }
                    )
                    continue
                break

            tool_results: list[dict] = []
            for tu in tool_uses:
                result_text = toolbox.dispatch(tu.name, tu.input)
                tool_results.append(
                    {"type": "tool_result", "tool_use_id": tu.id, "content": result_text}
                )
                self.console.print(f"[dim]tool[/] [bold]{tu.name}[/] {tu.input}")
            messages.append({"role": "user", "content": tool_results})

            if toolbox.proposal is not None:
                break

        return toolbox.proposal, steps, messages

    def _critique(self, ctx: IncidentContext, scratchpad: str, proposal: dict) -> str:
        resp = self.llm.call(
            role="critic",
            system=SYSTEM_PROMPT,
            messages=[
                {
                    "role": "user",
                    "content": (
                        f"You are reviewing another on-call engineer's diagnosis.\n\n"
                        f"Alert:\n{alert_message(ctx.alert)}\n\n"
                        f"Their observations:\n{scratchpad}\n\n"
                        f"Their proposal:\n{proposal}\n\n"
                        "In 3-5 sentences, identify the single weakest part of the "
                        "diagnosis or the strongest alternative hypothesis they failed "
                        "to rule out. Be concrete. If the diagnosis is solid, say so "
                        "and stop."
                    ),
                }
            ],
        )
        return "".join(b.text for b in resp.content if b.type == "text").strip()
