"""Run every scenario through the agent and produce a scorecard."""
from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from rich.console import Console
from rich.table import Table

from ..agent.consent import AutoApproveLowRisk
from ..agent.orchestrator import Orchestrator
from ..incidents import SCENARIOS, run_scenario
from .score import action_match, keyword_match, llm_judge


@dataclass
class BenchResult:
    scenario_id: str
    proposal: dict | None
    keyword_score: dict
    action_score: dict
    llm_score: dict | None
    steps: int
    tool_calls: int
    wall_seconds: float
    usage: dict
    critique: str | None
    consented: bool
    executed: bool
    recovered: bool | None


@dataclass
class BenchReport:
    results: list[BenchResult] = field(default_factory=list)

    def summary(self) -> dict:
        n = len(self.results)
        rc_hits = sum(1 for r in self.results if r.keyword_score.get("root_cause_hit"))
        rem_hits = sum(1 for r in self.results if r.keyword_score.get("remediation_hit"))
        action_hits = sum(1 for r in self.results if r.action_score.get("action_type_correct"))
        proposed = sum(1 for r in self.results if r.proposal is not None)
        consented = sum(1 for r in self.results if r.consented)
        executed = sum(1 for r in self.results if r.executed)
        recovered = sum(1 for r in self.results if r.recovered is True)
        llm_rc = [r.llm_score["root_cause"] for r in self.results if r.llm_score] or [0]
        return {
            "n": n,
            "proposed_pct": round(100 * proposed / n, 1) if n else 0,
            "root_cause_kw_pct": round(100 * rc_hits / n, 1) if n else 0,
            "remediation_kw_pct": round(100 * rem_hits / n, 1) if n else 0,
            "action_type_correct_pct": round(100 * action_hits / n, 1) if n else 0,
            "consented_pct": round(100 * consented / n, 1) if n else 0,
            "executed_pct": round(100 * executed / n, 1) if n else 0,
            "recovered_pct": round(100 * recovered / n, 1) if n else 0,
            "root_cause_llm_avg": round(sum(llm_rc) / len(llm_rc), 2),
            "avg_steps": round(sum(r.steps for r in self.results) / n, 1) if n else 0,
            "avg_tool_calls": round(sum(r.tool_calls for r in self.results) / n, 1) if n else 0,
            "avg_wall_s": round(sum(r.wall_seconds for r in self.results) / n, 1) if n else 0,
            "total_input_tokens": sum(r.usage.get("input_tokens", 0) for r in self.results),
            "total_output_tokens": sum(r.usage.get("output_tokens", 0) for r in self.results),
            "total_cache_read_tokens": sum(r.usage.get("cache_read", 0) for r in self.results),
        }


def run_bench(
    scenario_ids: list[str] | None = None,
    use_llm_judge: bool = True,
    out_path: Path | None = None,
    console: Console | None = None,
) -> BenchReport:
    console = console or Console()
    scenarios = SCENARIOS if not scenario_ids else [s for s in SCENARIOS if s.id in set(scenario_ids)]
    report = BenchReport()

    for s in scenarios:
        console.rule(f"[bold yellow]scenario: {s.id}")
        ctx = run_scenario(s)
        orch = Orchestrator(consent_broker=AutoApproveLowRisk(), console=console)
        t0 = time.time()
        inv = orch.run(ctx, scenario=s)
        wall = time.time() - t0
        kw = keyword_match(inv.proposal, s)
        act = action_match(inv.proposal, s, ctx)
        llm = llm_judge(inv.proposal, s) if use_llm_judge else None
        report.results.append(
            BenchResult(
                scenario_id=s.id,
                proposal=inv.proposal,
                keyword_score=kw,
                action_score=act,
                llm_score=llm,
                steps=inv.steps,
                tool_calls=len(inv.tool_calls),
                wall_seconds=wall,
                usage=inv.usage,
                critique=inv.critique,
                consented=bool(inv.consent and inv.consent.approved),
                executed=inv.execution is not None and inv.execution.outcome == "ok",
                recovered=inv.verification.recovered if inv.verification else None,
            )
        )

    _render_table(report, console)
    if out_path:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(
            json.dumps(
                {
                    "summary": report.summary(),
                    "results": [asdict(r) for r in report.results],
                },
                indent=2,
                default=str,
            )
        )
        console.print(f"[green]wrote[/] {out_path}")
    return report


def _render_table(report: BenchReport, console: Console) -> None:
    t = Table(title="oncall-agent eval")
    t.add_column("scenario")
    t.add_column("proposed?")
    t.add_column("rc_kw")
    t.add_column("rc_llm")
    t.add_column("action")
    t.add_column("consent")
    t.add_column("exec")
    t.add_column("recovered")
    t.add_column("steps")
    t.add_column("wall_s")
    for r in report.results:
        rc_llm = str(r.llm_score["root_cause"]) if r.llm_score else "-"
        recovered = "-" if r.recovered is None else ("yes" if r.recovered else "NO")
        t.add_row(
            r.scenario_id,
            "yes" if r.proposal else "NO",
            "hit" if r.keyword_score.get("root_cause_hit") else "miss",
            rc_llm,
            "ok" if r.action_score.get("action_type_correct") else "wrong",
            "yes" if r.consented else "no",
            "yes" if r.executed else "no",
            recovered,
            str(r.steps),
            f"{r.wall_seconds:.1f}",
        )
    console.print(t)
    console.print(report.summary())
