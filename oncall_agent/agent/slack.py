"""Fake Slack channel. Writes to a JSONL log and pretty-prints to stdout."""
from __future__ import annotations

import json
import time
from pathlib import Path

from rich.console import Console
from rich.panel import Panel


class SlackChannel:
    def __init__(self, log_path: Path | None = None, console: Console | None = None) -> None:
        self.log_path = Path(log_path) if log_path else None
        self.console = console or Console()
        self.posts: list[dict] = []

    def post(self, message: str) -> None:
        entry = {"ts": time.time(), "message": message}
        self.posts.append(entry)
        if self.log_path:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with self.log_path.open("a") as f:
                f.write(json.dumps(entry) + "\n")
        self.console.print(Panel(message, title="#incident-orders", border_style="cyan"))
