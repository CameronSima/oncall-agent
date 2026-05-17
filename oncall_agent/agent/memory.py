"""Layer 4: memory.

- `Scratchpad`: working memory. Every tool call appends an observation. The
  orchestrator can dump a compact summary back to the model when context grows.
- `EpisodicMemory`: long-term store of past resolved incidents. Scaffold uses
  keyword scoring; swap for embeddings later without changing the call sites.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Observation:
    ts: float
    tool: str
    args: dict
    result_summary: str


@dataclass
class Scratchpad:
    observations: list[Observation] = field(default_factory=list)

    def observe(self, tool: str, args: dict, result_summary: str) -> None:
        self.observations.append(
            Observation(ts=time.time(), tool=tool, args=args, result_summary=result_summary)
        )

    def render(self, max_chars: int = 4000) -> str:
        out: list[str] = []
        for o in self.observations:
            line = f"- {o.tool}({json.dumps(o.args)}) -> {o.result_summary}"
            out.append(line)
        text = "\n".join(out)
        if len(text) > max_chars:
            text = text[-max_chars:]
            text = "...[truncated]...\n" + text
        return text or "(no observations yet)"


@dataclass
class PastIncident:
    id: str
    alert_name: str
    root_cause: str
    remediation: str
    tags: list[str] = field(default_factory=list)


class EpisodicMemory:
    """Keyword-scored retrieval over past incidents.

    Stored as JSONL at `path`. Swap `search` for an embedding-based version
    without touching `agent/tools.py`.
    """

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._cache: list[PastIncident] | None = None

    def _load(self) -> list[PastIncident]:
        if self._cache is not None:
            return self._cache
        items: list[PastIncident] = []
        if self.path.exists():
            for line in self.path.read_text().splitlines():
                if not line.strip():
                    continue
                d = json.loads(line)
                items.append(PastIncident(**d))
        self._cache = items
        return items

    def add(self, incident: PastIncident) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a") as f:
            f.write(json.dumps(incident.__dict__) + "\n")
        self._cache = None

    def search(self, query: str, k: int = 3) -> list[dict]:
        tokens = set(re.findall(r"\w+", query.lower()))
        scored: list[tuple[int, PastIncident]] = []
        for inc in self._load():
            blob = f"{inc.alert_name} {inc.root_cause} {' '.join(inc.tags)}".lower()
            score = sum(1 for t in tokens if t in blob)
            if score:
                scored.append((score, inc))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [
            {
                "id": i.id,
                "alert_name": i.alert_name,
                "root_cause": i.root_cause,
                "remediation": i.remediation,
                "score": s,
            }
            for s, i in scored[:k]
        ]
