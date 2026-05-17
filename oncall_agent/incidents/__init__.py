"""Synthetic incident scenarios with ground-truth root causes."""
from .scenarios import SCENARIOS, Scenario
from .runner import run_scenario, IncidentContext

__all__ = ["SCENARIOS", "Scenario", "run_scenario", "IncidentContext"]
