"""Eval harness: run all scenarios, score the agent against ground truth."""
from .bench import run_bench, BenchResult

__all__ = ["run_bench", "BenchResult"]
