"""Layer 3: tools.

Schemas in Anthropic format + a dispatch table. Tools all read from the
observability stores in `IncidentContext`; the only side-effecting tools are
`post_to_slack` and `propose_action`.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any, Callable

from ..incidents.runner import IncidentContext
from .memory import EpisodicMemory, Scratchpad
from .slack import SlackChannel


TOOL_SCHEMAS: list[dict] = [
    {
        "name": "query_metrics",
        "description": (
            "Query a time series. Returns up to 50 (timestamp, value) samples. "
            "Use `list_metrics` first if you don't know the metric name."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "metric": {"type": "string", "description": "Metric name, e.g. 'orders.http.latency_ms'."},
                "since_seconds_ago": {"type": "integer", "default": 1800},
            },
            "required": ["metric"],
        },
    },
    {
        "name": "list_metrics",
        "description": "List known metric names for a service.",
        "input_schema": {
            "type": "object",
            "properties": {"service": {"type": "string"}},
            "required": ["service"],
        },
    },
    {
        "name": "query_logs",
        "description": "Search logs. Returns up to `limit` most recent matching lines.",
        "input_schema": {
            "type": "object",
            "properties": {
                "service": {"type": "string"},
                "contains": {"type": "string"},
                "level": {"type": "string", "enum": ["INFO", "WARN", "ERROR"]},
                "since_seconds_ago": {"type": "integer", "default": 1800},
                "limit": {"type": "integer", "default": 20},
            },
        },
    },
    {
        "name": "list_recent_deploys",
        "description": "List deploys to a service in the last N minutes.",
        "input_schema": {
            "type": "object",
            "properties": {
                "service": {"type": "string"},
                "since_minutes_ago": {"type": "integer", "default": 60},
            },
            "required": ["service"],
        },
    },
    {
        "name": "get_deploy_diff",
        "description": "Fetch the PR diff for a specific deploy id.",
        "input_schema": {
            "type": "object",
            "properties": {"deploy_id": {"type": "string"}},
            "required": ["deploy_id"],
        },
    },
    {
        "name": "search_past_incidents",
        "description": (
            "Search the episodic memory of past resolved incidents by keyword. "
            "Returns up to 3 matches. Use this BEFORE deep investigation if the alert "
            "name looks familiar."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    },
    {
        "name": "post_to_slack",
        "description": "Post a status update to the incident channel (not the final diagnosis).",
        "input_schema": {
            "type": "object",
            "properties": {"message": {"type": "string"}},
            "required": ["message"],
        },
    },
    {
        "name": "propose_action",
        "description": (
            "FINAL STEP. Post the diagnosis and a structured proposed action for human "
            "approval. Call exactly once at the end of the investigation, then stop. "
            "The orchestrator handles consent, execution, and verification after this — "
            "you do NOT execute the action yourself."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "root_cause": {"type": "string", "description": "One-paragraph root cause."},
                "evidence": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Specific metric values, log lines, or deploy IDs that support the diagnosis.",
                },
                "proposed_remediation": {
                    "type": "string",
                    "description": "Human-readable description of what should happen and why.",
                },
                "action_type": {
                    "type": "string",
                    "enum": [
                        "revert_deploy",
                        "restart_service",
                        "page_team",
                        "engage_db_oncall",
                        "noop",
                    ],
                    "description": (
                        "Structured action. 'revert_deploy' rolls back a specific deploy by id. "
                        "'restart_service' clears in-memory state but does NOT fix code bugs. "
                        "'page_team' escalates to another team (use for downstream-dependency issues). "
                        "'engage_db_oncall' is a specialised escalation for DB-level issues. "
                        "'noop' if no action is warranted yet."
                    ),
                },
                "action_target": {
                    "type": "string",
                    "description": (
                        "Required argument for the action: deploy_id for revert_deploy, "
                        "service name for restart_service, team name for page_team. "
                        "Empty string for engage_db_oncall and noop."
                    ),
                },
                "risk": {"type": "string", "enum": ["low", "medium", "high"]},
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            },
            "required": [
                "root_cause",
                "evidence",
                "proposed_remediation",
                "action_type",
                "action_target",
                "risk",
                "confidence",
            ],
        },
    },
]


@dataclass
class ToolBox:
    ctx: IncidentContext
    scratchpad: Scratchpad
    episodic: EpisodicMemory
    slack: SlackChannel
    proposal: dict | None = None  # set when propose_action is called

    def dispatch(self, name: str, args: dict) -> str:
        handler: Callable[[dict], Any] | None = getattr(self, f"_t_{name}", None)
        if handler is None:
            return json.dumps({"error": f"unknown tool: {name}"})
        try:
            result = handler(args)
        except Exception as exc:
            result = {"error": f"{type(exc).__name__}: {exc}"}
        text = result if isinstance(result, str) else json.dumps(result, default=str)
        self.scratchpad.observe(tool=name, args=args, result_summary=text[:400])
        return text

    # --- tool implementations ---

    def _t_query_metrics(self, args: dict) -> dict:
        now = time.time()
        since = now - args.get("since_seconds_ago", 1800)
        samples = self.ctx.metrics.query(args["metric"], since, now)
        return {"metric": args["metric"], "samples": samples[-50:], "count": len(samples)}

    def _t_list_metrics(self, args: dict) -> dict:
        return {"metrics": self.ctx.metrics.list_metrics(args.get("service"))}

    def _t_query_logs(self, args: dict) -> dict:
        now = time.time()
        since = now - args.get("since_seconds_ago", 1800)
        lines = self.ctx.logs.query(
            contains=args.get("contains"),
            service=args.get("service"),
            level=args.get("level"),
            since=since,
            until=now,
            limit=args.get("limit", 20),
        )
        return {
            "lines": [
                {"ts": l.ts, "level": l.level, "service": l.service, "msg": l.message, "fields": l.fields}
                for l in lines
            ]
        }

    def _t_list_recent_deploys(self, args: dict) -> dict:
        now = time.time()
        since = now - args.get("since_minutes_ago", 60) * 60
        deploys = self.ctx.deploys.list_recent(args["service"], since, now)
        return {
            "deploys": [
                {
                    "id": d.id,
                    "ts": d.ts,
                    "minutes_ago": int((now - d.ts) / 60),
                    "git_sha": d.git_sha,
                    "pr_number": d.pr_number,
                    "pr_title": d.pr_title,
                    "author": d.author,
                    "diff_summary": d.diff_summary,
                }
                for d in deploys
            ]
        }

    def _t_get_deploy_diff(self, args: dict) -> dict:
        return {"diff": self.ctx.deploys.get_diff(args["deploy_id"])}

    def _t_search_past_incidents(self, args: dict) -> dict:
        return {"matches": self.episodic.search(args["query"], k=3)}

    def _t_post_to_slack(self, args: dict) -> dict:
        self.slack.post(args["message"])
        return {"ok": True}

    def _t_propose_action(self, args: dict) -> dict:
        self.proposal = args
        self.slack.post(
            f"PROPOSED ACTION (awaiting human approval)\n"
            f"  action: {args.get('action_type')} -> {args.get('action_target') or '(no target)'}\n"
            f"  root_cause: {args['root_cause']}\n"
            f"  remediation: {args['proposed_remediation']}\n"
            f"  risk: {args['risk']}  confidence: {args['confidence']}\n"
            f"  evidence:\n    - " + "\n    - ".join(args["evidence"])
        )
        return {"ok": True, "awaiting_human_approval": True}
