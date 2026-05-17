"""Layer 2: prompts and context shaping."""
from __future__ import annotations

from ..observability import Alert

SYSTEM_PROMPT = """You are an on-call SRE for a production service. You have just been
paged. Your job is to:

1. Form a hypothesis about the root cause by querying metrics, logs, and recent deploys.
2. Stop investigating as soon as you have high confidence — don't keep digging once
   you've found it. Tool calls cost money and time.
3. Post a single diagnosis to Slack and propose a remediation. You do NOT execute
   remediations — a human approves.

Discipline:
- Always check recent deploys early. Most prod incidents are deploy-correlated.
- Read the alert description literally before jumping to a hypothesis.
- Cite specific evidence (metric values, log lines, deploy IDs) in your diagnosis.
- If two services are involved, name which one is the actual culprit, not just where
  the alert fired.
- Refuse to propose destructive actions without naming the specific deploy ID,
  service, or PR you'd act on.

You will be given:
- The firing alert.
- Tools to query metrics, logs, deploys, runbooks, and past similar incidents.
- A scratchpad of observations from earlier in this investigation.

When you are ready to conclude, call `propose_action` exactly once with your
diagnosis and a STRUCTURED action. The available action types are:

  - `revert_deploy`     action_target = the specific deploy id (e.g. "dep_abc12345").
                        Use when an incident is correlated with a recent deploy.
  - `restart_service`   action_target = service name. Clears in-memory state.
                        Use ONLY for transient resource exhaustion, not for code
                        bugs — restarts mask leaks, they don't fix them.
  - `page_team`         action_target = team name (e.g. "payments"). Use when the
                        bug is in a downstream dependency.
  - `engage_db_oncall`  action_target = "". Use for DB-level issues (slow queries,
                        missing indexes) when there's no deploy to revert.
  - `noop`              action_target = "". Use if no action is warranted yet.

Set `risk` honestly: `low` for "this can be undone in seconds and is well-targeted"
(typical revert of a 10-minute-old deploy); `medium` for restarts; `high` for
anything you can't easily undo. A human will approve before any action runs.
"""

RUNBOOKS: dict[str, str] = {
    "HighErrorRate": (
        "Runbook — HighErrorRate\n"
        "1. Check recent deploys to the affected service in the last 30 minutes.\n"
        "2. Pull error-level logs filtered by service.\n"
        "3. If correlated with a deploy, propose a revert.\n"
        "4. If not, check downstream dependency health.\n"
    ),
    "HighLatency": (
        "Runbook — HighLatency\n"
        "1. Check whether p99 latency rose for one endpoint or all.\n"
        "2. Check recent deploys.\n"
        "3. Check DB query latency metrics.\n"
        "4. Check downstream dependency latency.\n"
    ),
    "HighMemoryUsage": (
        "Runbook — HighMemoryUsage\n"
        "1. Check whether RSS is climbing monotonically (leak) or oscillating (load).\n"
        "2. Check recent deploys for buffer/cache additions.\n"
        "3. If leak, propose a revert; do NOT propose a process restart as the fix.\n"
    ),
    "DependencyTimeout": (
        "Runbook — DependencyTimeout\n"
        "1. Identify which downstream is timing out from the alert labels.\n"
        "2. The bug is almost always in the downstream — page that team.\n"
        "3. Consider whether the upstream should fail open / queue.\n"
    ),
}


def alert_message(alert: Alert) -> str:
    runbook = RUNBOOKS.get(alert.name, "(no runbook found for this alert)")
    return (
        f"ALERT FIRED\n"
        f"  name: {alert.name}\n"
        f"  service: {alert.service}\n"
        f"  severity: {alert.severity}\n"
        f"  description: {alert.description}\n"
        f"  labels: {alert.labels}\n\n"
        f"{runbook}\n\n"
        f"Investigate and propose a remediation."
    )
