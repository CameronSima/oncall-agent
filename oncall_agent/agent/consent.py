"""The CONSENT step in the action lifecycle.

A `ConsentBroker` looks at a `Proposal` and returns a `Consent` decision. The
broker is the security boundary: the LLM agent can never bypass it. We ship
three brokers:

  - `InteractiveConsent`  — prompts a human at the CLI (default for `run`).
  - `AutoApproveLowRisk`  — approves risk="low" proposals only (default for `bench`).
  - `PolicyConsent`       — rule-based, configured per action_type.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from rich.console import Console
from rich.prompt import Confirm

from .lifecycle import Consent, Proposal


class ConsentBroker(Protocol):
    def decide(self, proposal: Proposal) -> Consent: ...


@dataclass
class AutoApproveLowRisk:
    """Approves only risk='low' proposals. Useful for unattended eval runs."""

    approver: str = "policy:auto_low_risk"

    def decide(self, proposal: Proposal) -> Consent:
        if proposal.risk == "low" and proposal.confidence >= 0.6:
            return Consent(
                incident_id=proposal.incident_id,
                approved=True,
                approver=self.approver,
                reason=f"risk=low, confidence={proposal.confidence:.2f}",
            )
        return Consent(
            incident_id=proposal.incident_id,
            approved=False,
            approver=self.approver,
            reason=f"risk={proposal.risk}, confidence={proposal.confidence:.2f} fails policy",
        )


@dataclass
class PolicyConsent:
    """Rule-based: an allowlist of (action_type, max_risk) pairs."""

    allowed: dict[str, str]  # action_type -> highest risk auto-approved
    approver: str = "policy:rules"

    _RISK_RANK = {"low": 0, "medium": 1, "high": 2}

    def decide(self, proposal: Proposal) -> Consent:
        cap = self.allowed.get(proposal.action_type)
        if cap is None:
            return Consent(
                incident_id=proposal.incident_id,
                approved=False,
                approver=self.approver,
                reason=f"action_type '{proposal.action_type}' not in allowlist",
            )
        if self._RISK_RANK[proposal.risk] > self._RISK_RANK[cap]:
            return Consent(
                incident_id=proposal.incident_id,
                approved=False,
                approver=self.approver,
                reason=f"risk={proposal.risk} exceeds policy cap '{cap}' for {proposal.action_type}",
            )
        return Consent(
            incident_id=proposal.incident_id,
            approved=True,
            approver=self.approver,
            reason=f"{proposal.action_type} <= {cap}",
        )


@dataclass
class InteractiveConsent:
    console: Console
    approver: str = "human:cli"

    def decide(self, proposal: Proposal) -> Consent:
        self.console.rule("[bold yellow]CONSENT REQUIRED")
        self.console.print(f"  action: [bold]{proposal.action_type}[/]  target: {proposal.action_target}")
        self.console.print(f"  risk: {proposal.risk}  confidence: {proposal.confidence}")
        self.console.print(f"  rationale: {proposal.proposed_remediation}")
        approved = Confirm.ask("approve?", default=False, console=self.console)
        return Consent(
            incident_id=proposal.incident_id,
            approved=approved,
            approver=self.approver,
            reason="approved at CLI" if approved else "rejected at CLI",
        )
