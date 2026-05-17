"""EXECUTE + VERIFY steps.

`Executor.run` only performs side effects after the orchestrator has obtained
consent. `Verifier.run` re-drives synthetic traffic post-fix and checks whether
the alert condition cleared — gives the agent (and the eval) a true/false
recovery signal instead of trusting the diagnosis.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

from fastapi.testclient import TestClient

from ..app import bugs, main as app_main
from ..incidents.runner import IncidentContext
from ..incidents.scenarios import Scenario
from .lifecycle import Execution, Verification

ALLOWED_ACTIONS = (
    "revert_deploy",       # roll back a specific deploy_id
    "restart_service",     # restart a service (only fixes leaks, not bugs)
    "page_team",           # escalate to another team
    "engage_db_oncall",    # specialised escalation
    "noop",                # explicit "wait and observe"
)

# Map each bug flag to the action that actually resolves it. Used by the
# Verifier to know what side-effect to expect AND by tests to assert
# `executed_correctly`.
BUG_RESOLUTION: dict[str, tuple[str, str]] = {
    "memory_leak": ("revert_deploy", "deploy"),
    "bad_deploy_500": ("revert_deploy", "deploy"),
    "slow_db_query": ("engage_db_oncall", ""),
    "dependency_timeout": ("page_team", "payments"),
    "cache_stampede": ("restart_service", "orders"),
}


@dataclass
class Executor:
    ctx: IncidentContext
    scenario: Scenario

    def run(self, action_type: str, action_target: str) -> Execution:
        if action_type not in ALLOWED_ACTIONS:
            return Execution(
                incident_id="",
                action_type=action_type,
                action_target=action_target,
                outcome=f"error:disallowed_action",
                detail=f"{action_type} not in {ALLOWED_ACTIONS}",
            )

        if action_type == "revert_deploy":
            deploys = self.ctx.deploys.list_recent("orders", 0, 10**12)
            match = next((d for d in deploys if d.id == action_target), None)
            if match is None:
                return Execution(
                    incident_id="",
                    action_type=action_type,
                    action_target=action_target,
                    outcome="error:unknown_deploy",
                    detail=f"no deploy matching id={action_target!r}",
                )
            # If the scenario is bug_flag-backed, flipping the flag off models
            # the revert taking effect. Fixture-backed scenarios have no flag
            # to flip — the revert is recorded but recovery is not verifiable
            # by re-driving traffic.
            if self.scenario.bug_flag:
                setattr(bugs.FLAGS, self.scenario.bug_flag, False)
            return Execution(
                incident_id="",
                action_type=action_type,
                action_target=action_target,
                outcome="ok",
                detail=f"reverted deploy {match.id} (PR #{match.pr_number})",
            )

        if action_type == "restart_service":
            # Restarts clear in-memory state but don't fix bugs.
            app_main._LEAKED.clear()
            return Execution(
                incident_id="",
                action_type=action_type,
                action_target=action_target,
                outcome="ok",
                detail=f"restarted {action_target}; in-memory state cleared",
            )

        if action_type in {"page_team", "engage_db_oncall"}:
            return Execution(
                incident_id="",
                action_type=action_type,
                action_target=action_target,
                outcome="ok",
                detail=f"escalation sent ({action_type} {action_target})",
            )

        return Execution(
            incident_id="",
            action_type="noop",
            action_target="",
            outcome="ok",
            detail="no action taken",
        )


@dataclass
class Verifier:
    ctx: IncidentContext
    scenario: Scenario | None = None

    # Actions where it makes sense to verify recovery by re-driving traffic.
    _VERIFIABLE = {"revert_deploy", "restart_service"}

    def run(self, execution: Execution) -> Verification:
        if execution.action_type not in self._VERIFIABLE:
            return Verification(
                incident_id=execution.incident_id,
                recovered=None,
                summary=f"verification N/A for {execution.action_type}",
            )

        # Fixture-based scenarios have no live bug to "fix" by re-driving
        # traffic. We honestly report verification as N/A rather than
        # claiming false recovery because the traffic now looks clean.
        if self.scenario is not None and self.scenario.bug_flag is None:
            return Verification(
                incident_id=execution.incident_id,
                recovered=None,
                summary="fixture-based scenario; recovery not verifiable by replay",
            )

        # Re-drive a fresh batch of traffic with new item_ids so we can window
        # cleanly on post-fix metrics.
        start = time.time()
        client = TestClient(app_main.app)
        for i in range(500, 540):
            try:
                client.post("/order", params={"item_id": i, "qty": 1})
            except Exception:
                pass
        new_5xx = self.ctx.metrics.query("orders.http.5xx", since=start, until=time.time() + 1)
        new_timeouts = self.ctx.metrics.query(
            "orders.deps.payments.timeouts", since=start, until=time.time() + 1
        )
        if not new_5xx and not new_timeouts:
            return Verification(
                incident_id=execution.incident_id,
                recovered=True,
                summary="40 follow-up requests produced no 5xx and no dependency timeouts",
            )
        return Verification(
            incident_id=execution.incident_id,
            recovered=False,
            summary=(
                f"post-action traffic still showing errors: "
                f"5xx={len(new_5xx)} timeouts={len(new_timeouts)}"
            ),
        )
