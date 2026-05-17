"""CLI entry points."""
from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console

from .agent.consent import AutoApproveLowRisk, InteractiveConsent, PolicyConsent
from .agent.orchestrator import Orchestrator
from .evals.bench import run_bench
from .incidents import SCENARIOS, run_scenario
from .incidents.scenarios import by_id

app = typer.Typer(no_args_is_help=True)
console = Console()


@app.command(name="list")
def list_scenarios() -> None:
    """List available incident scenarios."""
    for s in SCENARIOS:
        console.print(
            f"[bold]{s.id}[/]  ({s.severity})  {s.name}  "
            f"[dim]expected action: {s.expected_action_type}[/]"
        )


@app.command(name="run")
def run_one(
    scenario_id: str,
    consent_mode: str = typer.Option(
        "interactive",
        "--consent",
        help="interactive | auto-low-risk | policy",
    ),
) -> None:
    """Run a single scenario end-to-end with the full lifecycle."""
    s = by_id(scenario_id)
    ctx = run_scenario(s)
    broker = {
        "interactive": InteractiveConsent(console=console),
        "auto-low-risk": AutoApproveLowRisk(),
        "policy": PolicyConsent(
            allowed={"revert_deploy": "low", "page_team": "medium", "engage_db_oncall": "medium"}
        ),
    }[consent_mode]
    orch = Orchestrator(consent_broker=broker, console=console)
    inv = orch.run(ctx, scenario=s)

    console.rule("[bold green]final proposal")
    console.print(inv.proposal or "[red]agent did not propose an action")
    if inv.consent:
        console.rule("[bold yellow]consent")
        console.print(inv.consent)
    if inv.execution:
        console.rule("[bold cyan]execution")
        console.print(inv.execution)
    if inv.verification:
        console.rule("[bold magenta]verification")
        console.print(inv.verification)
    if inv.critique:
        console.rule("[bold blue]critic")
        console.print(inv.critique)
    console.rule("[bold]audit")
    console.print(f"audit log: {inv.audit_log_path}")
    console.rule("[bold]usage")
    console.print(inv.usage)


@app.command(name="bench")
def bench(
    only: str | None = typer.Option(None, "--only", help="Comma-separated scenario ids."),
    out: Path = typer.Option(Path("oncall_agent_data/bench.json"), "--out"),
    no_llm_judge: bool = typer.Option(False, "--no-llm-judge"),
) -> None:
    """Run all scenarios and write a scorecard."""
    ids = [x.strip() for x in only.split(",")] if only else None
    run_bench(scenario_ids=ids, use_llm_judge=not no_llm_judge, out_path=out, console=console)


@app.command(name="serve")
def serve(port: int = 8000) -> None:
    """Run the toy orders service so you can poke it manually."""
    import uvicorn

    uvicorn.run("oncall_agent.app.main:app", host="127.0.0.1", port=port, reload=False)


if __name__ == "__main__":
    app()
