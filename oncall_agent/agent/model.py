"""Layer 1: model client.

Wraps the Anthropic SDK with prompt caching on the system prompt and tool
schemas, so a multi-step investigation only pays the system-prompt cost once.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

import anthropic


@dataclass
class ModelConfig:
    planner_model: str = field(default_factory=lambda: os.getenv("ONCALL_PLANNER_MODEL", "claude-opus-4-7"))
    worker_model: str = field(default_factory=lambda: os.getenv("ONCALL_WORKER_MODEL", "claude-sonnet-4-6"))
    max_tokens: int = 4096
    temperature: float = 0.2


class LLM:
    """Thin wrapper that always applies prompt caching to system + tools."""

    def __init__(self, cfg: ModelConfig | None = None) -> None:
        self.cfg = cfg or ModelConfig()
        self.client = anthropic.Anthropic()
        self.usage = {"input_tokens": 0, "output_tokens": 0, "cache_read": 0, "cache_creation": 0}

    def call(
        self,
        *,
        role: str,  # "planner" | "worker" | "critic"
        system: str,
        messages: list[dict],
        tools: list[dict] | None = None,
        tool_choice: dict | None = None,
    ) -> anthropic.types.Message:
        model = self.cfg.planner_model if role in {"planner", "critic"} else self.cfg.worker_model

        system_blocks = [
            {"type": "text", "text": system, "cache_control": {"type": "ephemeral"}},
        ]

        kwargs: dict = {
            "model": model,
            "max_tokens": self.cfg.max_tokens,
            "temperature": self.cfg.temperature,
            "system": system_blocks,
            "messages": messages,
        }
        if tools:
            # Cache the tool definitions too — they don't change across turns.
            cached_tools = [dict(t) for t in tools]
            cached_tools[-1]["cache_control"] = {"type": "ephemeral"}
            kwargs["tools"] = cached_tools
        if tool_choice:
            kwargs["tool_choice"] = tool_choice

        resp = self.client.messages.create(**kwargs)
        self._track(resp)
        return resp

    def _track(self, resp) -> None:
        u = resp.usage
        self.usage["input_tokens"] += u.input_tokens
        self.usage["output_tokens"] += u.output_tokens
        self.usage["cache_read"] += getattr(u, "cache_read_input_tokens", 0) or 0
        self.usage["cache_creation"] += getattr(u, "cache_creation_input_tokens", 0) or 0
