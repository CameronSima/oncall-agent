"""Action lifecycle: intent -> propose -> consent -> execute -> verify -> audit.

The architectural 5 layers (model / context / tools / memory / orchestration) describe
how the SYSTEM is built. The lifecycle below describes how a single REMEDIATION
flows through the system at runtime. The orchestrator drives it; this module just
defines the records and the append-only audit log.
"""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


def new_incident_id() -> str:
    return f"inc_{uuid.uuid4().hex[:10]}"


@dataclass
class Intent:
    incident_id: str
    alert_id: str
    alert_name: str
    service: str
    severity: str
    description: str
    ts: float = field(default_factory=time.time)


@dataclass
class Proposal:
    incident_id: str
    root_cause: str
    evidence: list[str]
    proposed_remediation: str
    action_type: str  # see executor.ALLOWED_ACTIONS
    action_target: str  # deploy_id | service | team | ""
    risk: str  # "low" | "medium" | "high"
    confidence: float
    ts: float = field(default_factory=time.time)


@dataclass
class Consent:
    incident_id: str
    approved: bool
    approver: str  # "human:alice" | "policy:auto_low_risk" | ...
    reason: str
    ts: float = field(default_factory=time.time)


@dataclass
class Execution:
    incident_id: str
    action_type: str
    action_target: str
    outcome: str  # "ok" | "skipped" | "error:<msg>"
    detail: str
    ts: float = field(default_factory=time.time)


@dataclass
class Verification:
    incident_id: str
    recovered: bool | None  # None = N/A (e.g. escalation actions)
    summary: str
    ts: float = field(default_factory=time.time)


class AuditLog:
    """Append-only JSONL log. Each row is `{ts, stage, payload}`."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.entries: list[dict] = []

    def write(self, stage: str, payload: Any) -> dict:
        body = asdict(payload) if hasattr(payload, "__dataclass_fields__") else payload
        entry = {"ts": time.time(), "stage": stage, "payload": body}
        self.entries.append(entry)
        with self.path.open("a") as f:
            f.write(json.dumps(entry, default=str) + "\n")
        return entry

    def render(self) -> str:
        return "\n".join(json.dumps(e, default=str) for e in self.entries)
