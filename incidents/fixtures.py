"""Declarative fixtures for incident scenarios.

Each scenario's fixture function populates `IncidentContext.metrics`, `.logs`,
and `.deploys` directly so we can express incident shapes the toy FastAPI app
can't reproduce (e.g. DNS failures, region outages, n+1 query patterns).

The agent only ever reads through the same tool surface, so it can't tell the
difference between fixture-injected data and traffic-driven data.
"""
from __future__ import annotations

import random
import uuid
from typing import Callable

from ..observability import Deploy, LogLine
from ..observability.memory import InMemoryDeploys, InMemoryLogs, InMemoryMetrics


# ---------- helpers ----------------------------------------------------------

def _steady(metrics: InMemoryMetrics, now: float, name: str, value: float, *, n: int = 40, span_s: int = 1800, jitter: float = 0.08) -> None:
    for i in range(n):
        v = value + (random.random() - 0.5) * value * jitter
        metrics.record(name, max(0.0, v), now - span_s + i * (span_s / n))


def _ramp(metrics: InMemoryMetrics, now: float, name: str, start: float, end: float, *, n: int = 24, span_s: int = 600) -> None:
    for i in range(n):
        v = start + (end - start) * (i / max(1, n - 1))
        metrics.record(name, v, now - span_s + i * (span_s / n))


def _spike(metrics: InMemoryMetrics, now: float, name: str, baseline: float, spike_v: float, *, started_s_ago: int = 300) -> None:
    _steady(metrics, now, name, baseline, n=30, span_s=1800)
    _ramp(metrics, now, name, baseline, spike_v, n=12, span_s=started_s_ago)


def _logs(logs: InMemoryLogs, now: float, *, level: str, service: str, message: str, n: int = 10, span_s: int = 300, fields: dict | None = None) -> None:
    for i in range(n):
        logs.append(
            LogLine(
                ts=now - span_s + i * (span_s / max(1, n)),
                level=level,
                service=service,
                message=message,
                fields=fields or {},
            )
        )


def _deploy(
    deploys: InMemoryDeploys,
    now: float,
    *,
    minutes_ago: int,
    sha: str,
    pr_number: int,
    title: str,
    author: str,
    diff: str,
    service: str = "orders",
    deploy_id: str | None = None,
    diff_summary: str = "+? -? across ? files",
) -> Deploy:
    d = Deploy(
        id=deploy_id or f"dep_{uuid.uuid4().hex[:8]}",
        ts=now - minutes_ago * 60,
        service=service,
        git_sha=sha,
        pr_number=pr_number,
        pr_title=title,
        author=author,
        diff_summary=diff_summary,
    )
    deploys.record(d, diff)
    return d


# ---------- fixtures: deploy-correlated reverts ------------------------------

def fx_cpu_spike_regex(metrics, logs, deploys, now) -> None:
    _deploy(
        deploys, now, minutes_ago=8, sha="def456", pr_number=4815,
        title="validation: stricter email regex", author="bob",
        diff=(
            "diff --git a/app/validate.py b/app/validate.py\n"
            "- EMAIL_RE = re.compile(r'^.+@.+$')\n"
            "+ EMAIL_RE = re.compile(r'^(\\w+\\.)+\\w+@(\\w+\\.)+\\w+$')  # vulnerable to catastrophic backtracking\n"
        ),
        diff_summary="+1 -1 across 1 file",
    )
    _spike(metrics, now, "orders.cpu.percent", baseline=22, spike_v=97, started_s_ago=480)
    _logs(logs, now, level="WARN", service="orders",
          message="request /validate took 2.3s (CPU bound)", n=18, span_s=420,
          fields={"endpoint": "/validate"})


def fx_log_volume_explosion(metrics, logs, deploys, now) -> None:
    _deploy(
        deploys, now, minutes_ago=5, sha="aa11bb", pr_number=4901,
        title="debug: log full request body for audit", author="carol",
        diff=(
            "diff --git a/app/order.py b/app/order.py\n"
            "+ logger.debug(f'request body: {request.body!r}')\n"
        ),
    )
    _spike(metrics, now, "orders.logs.bytes_per_min", baseline=200_000, spike_v=24_000_000, started_s_ago=300)
    _spike(metrics, now, "host.disk.used_pct", baseline=55, spike_v=94, started_s_ago=300)
    _logs(logs, now, level="WARN", service="orders",
          message="logger queue saturated, dropping records", n=12, span_s=240)


def fx_n_plus_one(metrics, logs, deploys, now) -> None:
    _deploy(
        deploys, now, minutes_ago=12, sha="cc22dd", pr_number=5003,
        title="checkout: hydrate line items eagerly (oops)", author="dave",
        diff=(
            "diff --git a/app/checkout.py b/app/checkout.py\n"
            "- items = db.fetch_items(order.item_ids)\n"
            "+ items = [db.fetch_item(i) for i in order.item_ids]\n"
        ),
    )
    _spike(metrics, now, "orders.http.latency_ms", baseline=45, spike_v=1800, started_s_ago=600)
    _spike(metrics, now, "db.queries_per_request", baseline=2.0, spike_v=42.0, started_s_ago=600)


def fx_cache_disabled(metrics, logs, deploys, now) -> None:
    _deploy(
        deploys, now, minutes_ago=6, sha="ee33ff", pr_number=5110,
        title="refactor: simplify product service", author="erin",
        diff=(
            "diff --git a/app/products.py b/app/products.py\n"
            "- @lru_cache(maxsize=10000)\n"
            "  def get_product(pid): ...\n"
        ),
    )
    _spike(metrics, now, "orders.cache.miss_rate", baseline=0.02, spike_v=0.98, started_s_ago=360)
    _spike(metrics, now, "db.connections.active", baseline=12, spike_v=98, started_s_ago=360)


def fx_goroutine_leak(metrics, logs, deploys, now) -> None:
    _deploy(
        deploys, now, minutes_ago=22, sha="11gghh", pr_number=5201,
        title="async: spawn worker for audit event", author="frank",
        diff=(
            "diff --git a/app/audit.py b/app/audit.py\n"
            "+ go func() { audit_async(event) }()  // never bounded\n"
        ),
    )
    _ramp(metrics, now, "orders.runtime.goroutines", start=120, end=18_000, n=40, span_s=1320)
    _logs(logs, now, level="WARN", service="orders",
          message="scheduler latency >100ms (goroutine pressure)", n=10, span_s=600)


def fx_canary_only_failures(metrics, logs, deploys, now) -> None:
    # Two deploys: main stable + a fresh canary. Only canary fails.
    _deploy(
        deploys, now, minutes_ago=240, sha="stableA", pr_number=4700,
        title="docs: README cleanup", author="alice",
        diff="diff --git a/README.md\n- old\n+ new\n",
        deploy_id="dep_main_stable",
        diff_summary="+8 -3 across 1 file",
    )
    _deploy(
        deploys, now, minutes_ago=7, sha="canaryB", pr_number=5305,
        title="checkout: new pricing engine (canary)", author="grace",
        diff=(
            "diff --git a/app/pricing.py b/app/pricing.py\n"
            "+ price = engine_v2.compute(item)  # NEW\n"
        ),
        deploy_id="dep_canary_pricing",
    )
    _steady(metrics, now, "orders.http.5xx{rollout=stable}", value=0, n=30)
    _spike(metrics, now, "orders.http.5xx{rollout=canary}", baseline=0, spike_v=42, started_s_ago=360)
    _logs(logs, now, level="ERROR", service="orders",
          message="pricing.engine_v2: KeyError 'discount_code'", n=14, span_s=300,
          fields={"rollout": "canary"})


def fx_broken_feature_flag(metrics, logs, deploys, now) -> None:
    _deploy(
        deploys, now, minutes_ago=4, sha="ff44gg", pr_number=5409,
        title="feat: enable new_address_form by default", author="henry",
        diff=(
            "diff --git a/app/flags.py b/app/flags.py\n"
            "- 'new_address_form': False,\n"
            "+ 'new_address_form': True,\n"
        ),
    )
    _spike(metrics, now, "orders.http.5xx", baseline=0, spike_v=18, started_s_ago=240)
    _logs(logs, now, level="ERROR", service="orders",
          message="new_address_form: missing field 'state' for non-US users", n=20, span_s=240,
          fields={"flag": "new_address_form"})


# ---------- fixtures: infra / external escalations ---------------------------

def fx_dns_failure(metrics, logs, deploys, now) -> None:
    _spike(metrics, now, "orders.http.5xx", baseline=0, spike_v=120, started_s_ago=180)
    _spike(metrics, now, "platform.dns.resolution_failures", baseline=0, spike_v=400, started_s_ago=180)
    _logs(logs, now, level="ERROR", service="orders",
          message="dns: temporary failure in name resolution for payments-svc.internal",
          n=22, span_s=200)
    _logs(logs, now, level="ERROR", service="checkout",
          message="dns: temporary failure in name resolution for users-svc.internal",
          n=15, span_s=200)


def fx_upstream_rate_limit(metrics, logs, deploys, now) -> None:
    _spike(metrics, now, "orders.deps.payments.http_status{code=429}", baseline=0, spike_v=80, started_s_ago=300)
    _steady(metrics, now, "orders.deps.payments.http_status{code=200}", value=2)
    _logs(logs, now, level="ERROR", service="orders",
          message="payments-svc returned 429 Too Many Requests (X-RateLimit-Reset: 60s)",
          n=18, span_s=280, fields={"upstream": "payments"})


def fx_ssl_cert_expired(metrics, logs, deploys, now) -> None:
    _spike(metrics, now, "orders.deps.users.tls_errors", baseline=0, spike_v=60, started_s_ago=240)
    _logs(logs, now, level="ERROR", service="orders",
          message="tls: x509: certificate has expired or is not yet valid: users-svc.internal",
          n=20, span_s=240, fields={"upstream": "users"})


def fx_disk_full(metrics, logs, deploys, now) -> None:
    _ramp(metrics, now, "host.disk.used_pct", start=88, end=100, n=24, span_s=900)
    _logs(logs, now, level="ERROR", service="orders",
          message="failed to write log: no space left on device", n=25, span_s=300)
    _logs(logs, now, level="ERROR", service="orders",
          message="checkout.write_receipt: IOError ENOSPC", n=12, span_s=300)


def fx_region_partial_outage(metrics, logs, deploys, now) -> None:
    _steady(metrics, now, "orders.http.5xx{region=us-west}", value=0)
    _spike(metrics, now, "orders.http.5xx{region=us-east}", baseline=1, spike_v=180, started_s_ago=420)
    _logs(logs, now, level="ERROR", service="orders",
          message="connection refused to db-primary.us-east.internal", n=20, span_s=400,
          fields={"region": "us-east"})
    _logs(logs, now, level="INFO", service="platform-status",
          message="cloud provider status: us-east-1 EBS degraded (incident PVK-2025-0517)",
          n=2, span_s=300)


# ---------- fixtures: tricky / red herrings ----------------------------------

def fx_coincidental_deploy(metrics, logs, deploys, now) -> None:
    # Deploy looks suspicious (10m ago) but it's docs-only.
    _deploy(
        deploys, now, minutes_ago=10, sha="docsOnly", pr_number=5500,
        title="docs: explain checkout flow", author="ivy",
        diff=(
            "diff --git a/docs/checkout.md b/docs/checkout.md\n"
            "+ ## Checkout flow\n"
            "+ The checkout endpoint accepts ...\n"
        ),
        diff_summary="+38 -2 across 1 file (docs only)",
    )
    # The REAL issue: a slow DB query started 25 minutes ago, before the deploy.
    _spike(metrics, now, "orders.http.latency_ms", baseline=40, spike_v=3500, started_s_ago=1500)
    _spike(metrics, now, "db.query_p99_ms", baseline=15, spike_v=4200, started_s_ago=1500)
    _logs(logs, now, level="WARN", service="orders",
          message="slow query: SELECT * FROM orders WHERE item_id=$1 took 4.1s", n=18, span_s=900)


def fx_cron_burst_expected(metrics, logs, deploys, now) -> None:
    # Errors only fire in a tight burst every hour at :00. Misconfigured alert.
    minutes_into_hour = int(((now % 3600) // 60))
    # backfill three previous hours with bursts at :00
    for hours_ago in (3, 2, 1):
        burst_t = now - (hours_ago * 3600) - (minutes_into_hour * 60)
        for i in range(8):
            metrics.record("orders.http.5xx", 6, burst_t + i * 4)
        for i in range(6):
            logs.append(
                LogLine(
                    ts=burst_t + i * 5, level="ERROR", service="orders",
                    message="batch_recompute: connection reset (expected, retried)",
                    fields={"job": "nightly_recompute"},
                )
            )
    # Current burst still ongoing
    for i in range(8):
        metrics.record("orders.http.5xx", 6, now - 240 + i * 25)
    for i in range(6):
        logs.append(
            LogLine(
                ts=now - 240 + i * 30, level="ERROR", service="orders",
                message="batch_recompute: connection reset (expected, retried)",
                fields={"job": "nightly_recompute"},
            )
        )
    # All requests outside the burst window are clean.
    _steady(metrics, now, "orders.http.2xx", value=180, n=40)


def fx_retry_storm(metrics, logs, deploys, now) -> None:
    # Upstream "checkout" service is retrying us aggressively due to its bug.
    _spike(metrics, now, "orders.http.5xx", baseline=0, spike_v=60, started_s_ago=300)
    _spike(metrics, now, "orders.http.requests_per_min{source=checkout}", baseline=200, spike_v=4200, started_s_ago=300)
    _steady(metrics, now, "orders.http.requests_per_min{source=web}", value=180)
    _logs(logs, now, level="WARN", service="orders",
          message="duplicate request idempotency_key=ck_xxx from checkout-svc (retry #5)",
          n=22, span_s=280, fields={"source": "checkout"})
    _logs(logs, now, level="ERROR", service="orders",
          message="request queue full, rejecting requests from source=checkout",
          n=12, span_s=280, fields={"source": "checkout"})


def fx_partial_outage_redundant(metrics, logs, deploys, now) -> None:
    # 1 of 3 db replicas unhealthy but reads still served. No user impact.
    _steady(metrics, now, "db.replicas.healthy", value=2, n=30)
    _steady(metrics, now, "db.replicas.total", value=3, n=30)
    _steady(metrics, now, "orders.http.5xx", value=0, n=40)
    _steady(metrics, now, "orders.http.latency_ms", value=42, n=40)
    _logs(logs, now, level="WARN", service="orders",
          message="db-replica-3 marked unhealthy by haproxy (2/3 still serving)",
          n=4, span_s=300)


# ---------- registry ---------------------------------------------------------

FIXTURES: dict[str, Callable] = {
    "fx_cpu_spike_regex": fx_cpu_spike_regex,
    "fx_log_volume_explosion": fx_log_volume_explosion,
    "fx_n_plus_one": fx_n_plus_one,
    "fx_cache_disabled": fx_cache_disabled,
    "fx_goroutine_leak": fx_goroutine_leak,
    "fx_canary_only_failures": fx_canary_only_failures,
    "fx_broken_feature_flag": fx_broken_feature_flag,
    "fx_dns_failure": fx_dns_failure,
    "fx_upstream_rate_limit": fx_upstream_rate_limit,
    "fx_ssl_cert_expired": fx_ssl_cert_expired,
    "fx_disk_full": fx_disk_full,
    "fx_region_partial_outage": fx_region_partial_outage,
    "fx_coincidental_deploy": fx_coincidental_deploy,
    "fx_cron_burst_expected": fx_cron_burst_expected,
    "fx_retry_storm": fx_retry_storm,
    "fx_partial_outage_redundant": fx_partial_outage_redundant,
}
