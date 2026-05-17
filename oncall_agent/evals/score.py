"""Scoring.

Two judges:
  - `keyword_match`: a fast, cheap heuristic for CI / smoke runs.
  - `llm_judge`: a Claude call that grades root-cause and remediation
    similarity to the ground truth. Used for headline numbers.
"""
from __future__ import annotations

import json
import re

from ..agent.model import LLM
from ..incidents.runner import IncidentContext
from ..incidents.scenarios import Scenario


def action_match(
    proposal: dict | None, scenario: Scenario, ctx: IncidentContext
) -> dict:
    """Did the agent pick the right structured action for this scenario?

    Splits into two questions:
      - action_type_correct: did `action_type` match `expected_action_type`?
      - action_target_plausible: does the target look like the right KIND of thing
        (a real deploy id from this incident's deploys, a team name, etc.)?
    """
    if proposal is None:
        return {"action_type_correct": False, "action_target_plausible": False}
    action_type = proposal.get("action_type")
    target = proposal.get("action_target", "")
    type_ok = action_type == scenario.expected_action_type

    kind = scenario.expected_action_target_kind
    if kind == "deploy_id":
        known = {d.id for d in ctx.deploys.list_recent("orders", 0, 10**12)}
        target_ok = target in known
    elif kind == "team":
        target_ok = bool(target) and target.lower() in {"payments", "payments-svc", "payments team"}
    elif kind == "service":
        target_ok = bool(target)
    else:
        target_ok = target == ""

    return {"action_type_correct": type_ok, "action_target_plausible": target_ok}


def keyword_match(proposal: dict | None, scenario: Scenario) -> dict:
    if proposal is None:
        return {"root_cause_hit": False, "remediation_hit": False, "method": "keyword"}

    def hit(text: str, target: str) -> bool:
        tokens = set(re.findall(r"\w{4,}", target.lower())) - {"that", "from", "with", "this", "have"}
        return sum(1 for t in tokens if t in text.lower()) >= max(2, len(tokens) // 3)

    rc = proposal.get("root_cause", "")
    rem = proposal.get("proposed_remediation", "")
    return {
        "root_cause_hit": hit(rc, scenario.expected_root_cause),
        "remediation_hit": hit(rem, scenario.expected_remediation),
        "method": "keyword",
    }


JUDGE_PROMPT = """You are grading an on-call engineer's diagnosis against the
ground-truth root cause of an incident.

Score two things on a 0-2 scale:
  - root_cause: 2 = identifies the same root cause; 1 = partially correct or
    correct cause but misattributed; 0 = wrong.
  - remediation: 2 = same remediation; 1 = reasonable alternative; 0 = wrong
    or dangerous.

Respond with strict JSON: {"root_cause": int, "remediation": int, "rationale": str}.
"""


def llm_judge(proposal: dict | None, scenario: Scenario, llm: LLM | None = None) -> dict:
    if proposal is None:
        return {"root_cause": 0, "remediation": 0, "rationale": "no proposal", "method": "llm"}
    llm = llm or LLM()
    user = (
        f"Scenario: {scenario.name}\n"
        f"Ground-truth root cause: {scenario.expected_root_cause}\n"
        f"Ground-truth remediation: {scenario.expected_remediation}\n\n"
        f"Agent's root cause: {proposal.get('root_cause')}\n"
        f"Agent's remediation: {proposal.get('proposed_remediation')}\n"
    )
    resp = llm.call(
        role="critic",
        system=JUDGE_PROMPT,
        messages=[{"role": "user", "content": user}],
    )
    text = "".join(b.text for b in resp.content if b.type == "text").strip()
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.M).strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        data = {"root_cause": 0, "remediation": 0, "rationale": f"parse_error: {text[:200]}"}
    data["method"] = "llm"
    return data
