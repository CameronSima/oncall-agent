"""The five-layer LLM agent.

- `model`       Layer 1: Anthropic client, model selection, prompt caching.
- `prompts`     Layer 2: system prompt, runbook injection, context budgeting.
- `tools`       Layer 3: tool schemas + dispatch.
- `memory`      Layer 4: working scratchpad + episodic past-incident store.
- `orchestrator` Layer 5: plan -> investigate -> critique -> propose loop.
"""
from .orchestrator import Orchestrator, Investigation

__all__ = ["Orchestrator", "Investigation"]
