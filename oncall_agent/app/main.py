"""Toy 'orders' FastAPI service.

Records its own metrics + logs into the in-memory observability stores so the
agent can investigate it. In a real deployment these would be scraped by
Prometheus / shipped to Loki.
"""
from __future__ import annotations

import random
import time

from fastapi import FastAPI, HTTPException

from ..observability import InMemoryLogs, InMemoryMetrics, LogLine
from . import bugs

app = FastAPI(title="orders")

# Singleton observability sinks. The CLI / scenario runner wires these to the
# same instances the agent reads from.
METRICS = InMemoryMetrics()
LOGS = InMemoryLogs()

_LEAKED: list[bytes] = []  # intentional: feeds the memory_leak bug


def _log(level: str, msg: str, **fields) -> None:
    LOGS.append(LogLine(ts=time.time(), level=level, service="orders", message=msg, fields=fields))


@app.get("/health")
def health() -> dict:
    return {"ok": True}


@app.post("/order")
def order(item_id: int, qty: int = 1) -> dict:
    start = time.time()
    if bugs.FLAGS.memory_leak:
        _LEAKED.append(b"x" * 1_000_000)
        METRICS.record("orders.process.rss_bytes", sum(len(b) for b in _LEAKED))

    if bugs.FLAGS.bad_deploy_500 and item_id % 3 == 0:
        _log("ERROR", "NullPointerException at checkout.py:84", item_id=item_id)
        METRICS.record("orders.http.5xx", 1)
        raise HTTPException(500, "internal error")

    if bugs.FLAGS.slow_db_query:
        # In reality we'd `time.sleep(5)`. For evals we record the timing.
        latency = 5.0 + random.random()
    elif bugs.FLAGS.dependency_timeout:
        latency = 30.0
        _log("ERROR", "payments-svc: timeout after 30s", item_id=item_id)
        METRICS.record("orders.deps.payments.timeouts", 1)
        raise HTTPException(504, "payments timeout")
    else:
        latency = 0.02 + random.random() * 0.05

    METRICS.record("orders.http.latency_ms", latency * 1000)
    METRICS.record("orders.http.2xx", 1)
    _log("INFO", "order placed", item_id=item_id, qty=qty, latency_ms=int(latency * 1000))
    return {"order_id": random.randint(1000, 9999), "latency_ms": int(latency * 1000)}
