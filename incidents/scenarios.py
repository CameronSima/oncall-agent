"""The eval set. Each scenario is one incident with a known root cause and a
known correct structured action.

A scenario is backed by EITHER a `bug_flag` (drives the FastAPI orders service
via synthetic traffic) OR a `fixture` (declarative metric/log/deploy injection
from `fixtures.py`). The agent's tool surface is identical in both cases.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, kw_only=True)
class Scenario:
    id: str
    name: str
    severity: str  # "page" | "ticket"
    alert_name: str
    alert_description: str
    expected_root_cause: str
    expected_remediation: str
    expected_action_type: str  # one of executor.ALLOWED_ACTIONS
    expected_action_target_kind: str  # "deploy_id" | "service" | "team" | ""
    bug_flag: str | None = None
    plant_deploy: bool = False
    fixture: str | None = None  # name in fixtures.FIXTURES


SCENARIOS: list[Scenario] = [
    # ---------- existing 4 (bug_flag / FastAPI traffic) -----------
    Scenario(
        id="memory_leak",
        name="Orders service RSS climbing",
        severity="ticket",
        alert_name="HighMemoryUsage",
        alert_description="orders process RSS > 2GB and rising for 15m",
        bug_flag="memory_leak",
        plant_deploy=True,
        expected_root_cause=(
            "A recent deploy introduced a per-request memory allocation that is "
            "never released (memory leak in /order)."
        ),
        expected_remediation="Revert the most recent orders deploy.",
        expected_action_type="revert_deploy",
        expected_action_target_kind="deploy_id",
    ),
    Scenario(
        id="bad_deploy_500",
        name="5xx spike on /order",
        severity="page",
        alert_name="HighErrorRate",
        alert_description="orders 5xx rate > 5% for 5m",
        bug_flag="bad_deploy_500",
        plant_deploy=True,
        expected_root_cause=(
            "The most recent orders deploy introduced a NullPointerException on "
            "the checkout path for a subset of item_ids."
        ),
        expected_remediation="Revert the most recent orders deploy.",
        expected_action_type="revert_deploy",
        expected_action_target_kind="deploy_id",
    ),
    Scenario(
        id="slow_db_query",
        name="Latency p99 blown",
        severity="page",
        alert_name="HighLatency",
        alert_description="orders p99 > 2s for 10m",
        bug_flag="slow_db_query",
        plant_deploy=False,
        expected_root_cause=(
            "A database query on the checkout path is taking ~5s instead of ~20ms. "
            "No recent deploy — likely missing index or table bloat."
        ),
        expected_remediation=(
            "Engage the DB on-call. Check pg_stat_statements for the slow query; "
            "consider re-adding the index on orders.item_id."
        ),
        expected_action_type="engage_db_oncall",
        expected_action_target_kind="",
    ),
    Scenario(
        id="dependency_timeout",
        name="Payments dependency down",
        severity="page",
        alert_name="DependencyTimeout",
        alert_description="orders -> payments timeout rate > 50% for 5m",
        bug_flag="dependency_timeout",
        plant_deploy=False,
        expected_root_cause=(
            "The downstream payments service is timing out on every request from "
            "orders. The bug is in payments, not orders."
        ),
        expected_remediation=(
            "Page the payments on-call. Consider failing orders open to a queue "
            "while payments is down."
        ),
        expected_action_type="page_team",
        expected_action_target_kind="team",
    ),

    # ---------- 6 deploy-correlated reverts (fixture-based) -----------
    Scenario(
        id="cpu_spike_regex",
        name="CPU pegged after validation change",
        severity="page",
        alert_name="HighCPU",
        alert_description="orders.cpu.percent > 90 for 8m",
        fixture="fx_cpu_spike_regex",
        expected_root_cause=(
            "A new email regex with catastrophic backtracking deployed 8m ago is "
            "causing CPU-bound stalls on the /validate endpoint."
        ),
        expected_remediation="Revert the validation regex deploy.",
        expected_action_type="revert_deploy",
        expected_action_target_kind="deploy_id",
    ),
    Scenario(
        id="log_volume_explosion",
        name="Disk filling from log explosion",
        severity="ticket",
        alert_name="HighLogVolume",
        alert_description="orders log volume 100x baseline; host disk > 90%",
        fixture="fx_log_volume_explosion",
        expected_root_cause=(
            "A recent deploy added a per-request debug log of the full request "
            "body, blowing log volume and filling the disk."
        ),
        expected_remediation="Revert the deploy that added the debug log.",
        expected_action_type="revert_deploy",
        expected_action_target_kind="deploy_id",
    ),
    Scenario(
        id="n_plus_one_query",
        name="Latency p99 with DB query explosion",
        severity="page",
        alert_name="HighLatency",
        alert_description="orders p99 > 1500ms for 10m; db.queries_per_request > 20",
        fixture="fx_n_plus_one",
        expected_root_cause=(
            "A recent deploy changed batch line-item fetch into a per-item loop, "
            "producing an N+1 query pattern on checkout."
        ),
        expected_remediation="Revert the checkout deploy.",
        expected_action_type="revert_deploy",
        expected_action_target_kind="deploy_id",
    ),
    Scenario(
        id="cache_disabled",
        name="DB load spike, cache miss rate 98%",
        severity="page",
        alert_name="HighCacheMissRate",
        alert_description="orders.cache.miss_rate > 0.5 for 6m; db connections saturating",
        fixture="fx_cache_disabled",
        expected_root_cause=(
            "A refactor deploy removed the @lru_cache decorator on get_product, "
            "sending every product lookup to the DB."
        ),
        expected_remediation="Revert the product service refactor.",
        expected_action_type="revert_deploy",
        expected_action_target_kind="deploy_id",
    ),
    Scenario(
        id="goroutine_leak",
        name="Goroutine count unbounded after deploy",
        severity="ticket",
        alert_name="HighThreadCount",
        alert_description="orders.runtime.goroutines climbing monotonically for 20m",
        fixture="fx_goroutine_leak",
        expected_root_cause=(
            "A recent deploy spawns an unbounded goroutine per audit event, never "
            "joined."
        ),
        expected_remediation="Revert the audit-event deploy.",
        expected_action_type="revert_deploy",
        expected_action_target_kind="deploy_id",
    ),
    Scenario(
        id="canary_only_failures",
        name="5xx on canary rollout only",
        severity="page",
        alert_name="HighErrorRate",
        alert_description="orders 5xx rate > 5% but ONLY on rollout=canary",
        fixture="fx_canary_only_failures",
        expected_root_cause=(
            "The new pricing engine deployed to the canary slice is throwing on "
            "missing 'discount_code' keys. The stable rollout is unaffected."
        ),
        expected_remediation="Revert the canary deploy (NOT the stable deploy).",
        expected_action_type="revert_deploy",
        expected_action_target_kind="deploy_id",
    ),
    Scenario(
        id="broken_feature_flag",
        name="5xx after feature flag flipped on",
        severity="page",
        alert_name="HighErrorRate",
        alert_description="orders 5xx rate up since 4m ago",
        fixture="fx_broken_feature_flag",
        expected_root_cause=(
            "A deploy 4m ago flipped the new_address_form flag default to True. "
            "Non-US users hit a missing-field error in the new form."
        ),
        expected_remediation=(
            "Revert the deploy that flipped the flag. (We don't have a "
            "disable_feature_flag action in this environment.)"
        ),
        expected_action_type="revert_deploy",
        expected_action_target_kind="deploy_id",
    ),

    # ---------- 5 infrastructure / external escalations -----------
    Scenario(
        id="dns_failure",
        name="DNS resolution failing platform-wide",
        severity="page",
        alert_name="HighDNSFailureRate",
        alert_description="platform.dns.resolution_failures > 100/min across multiple services",
        fixture="fx_dns_failure",
        expected_root_cause=(
            "DNS resolution for internal services is failing across multiple "
            "callers. This is an infrastructure-level issue, not an orders bug."
        ),
        expected_remediation="Page the platform/infra team.",
        expected_action_type="page_team",
        expected_action_target_kind="team",
    ),
    Scenario(
        id="upstream_rate_limit",
        name="Payments returning 429s",
        severity="page",
        alert_name="UpstreamRateLimited",
        alert_description="orders.deps.payments.http_status{code=429} climbing",
        fixture="fx_upstream_rate_limit",
        expected_root_cause=(
            "The payments upstream is rate-limiting us (429s). The bug, if any, "
            "is on the payments side or in our request rate."
        ),
        expected_remediation="Page the payments team.",
        expected_action_type="page_team",
        expected_action_target_kind="team",
    ),
    Scenario(
        id="ssl_cert_expired",
        name="TLS handshake failures to users-svc",
        severity="page",
        alert_name="TLSFailure",
        alert_description="orders.deps.users.tls_errors > 50/min",
        fixture="fx_ssl_cert_expired",
        expected_root_cause=(
            "The TLS certificate on users-svc has expired. Bug is in platform / "
            "users-svc, not orders."
        ),
        expected_remediation="Page the platform team to rotate the cert.",
        expected_action_type="page_team",
        expected_action_target_kind="team",
    ),
    Scenario(
        id="disk_full",
        name="Host disk full",
        severity="page",
        alert_name="DiskFull",
        alert_description="host.disk.used_pct at 100%; ENOSPC errors in logs",
        fixture="fx_disk_full",
        expected_root_cause=(
            "The host disk is full. orders cannot write logs or receipts. "
            "Needs disk cleanup or a larger volume, not a code change."
        ),
        expected_remediation="Page the platform team to clean disk / grow volume.",
        expected_action_type="page_team",
        expected_action_target_kind="team",
    ),
    Scenario(
        id="region_partial_outage",
        name="us-east region degraded",
        severity="page",
        alert_name="RegionUnhealthy",
        alert_description="orders 5xx high in us-east only; us-west clean",
        fixture="fx_region_partial_outage",
        expected_root_cause=(
            "The us-east cloud region is degraded (provider-side incident). "
            "us-west is unaffected. Not an orders bug."
        ),
        expected_remediation="Page the platform team; consider shifting traffic to us-west.",
        expected_action_type="page_team",
        expected_action_target_kind="team",
    ),

    # ---------- 5 tricky / red herrings -----------
    Scenario(
        id="coincidental_deploy",
        name="Latency spike — recent deploy is a red herring",
        severity="page",
        alert_name="HighLatency",
        alert_description="orders p99 > 2s for 15m; a docs deploy went out 10m ago",
        fixture="fx_coincidental_deploy",
        expected_root_cause=(
            "The recent deploy is docs-only — it did not change code. The real "
            "cause is a slow DB query that started ~25m ago, before the deploy."
        ),
        expected_remediation=(
            "Do NOT revert. Engage the DB on-call to investigate the slow query."
        ),
        expected_action_type="engage_db_oncall",
        expected_action_target_kind="",
    ),
    Scenario(
        id="cron_burst_expected",
        name="Hourly error burst — alert is misconfigured",
        severity="ticket",
        alert_name="HighErrorRate",
        alert_description="orders 5xx rate briefly > 5% at the top of every hour",
        fixture="fx_cron_burst_expected",
        expected_root_cause=(
            "Errors fire only during the nightly_recompute batch job at :00 each "
            "hour and are retried successfully. No user impact. The alert "
            "threshold is too tight for this expected pattern."
        ),
        expected_remediation=(
            "No action required. Tune the alert to exclude the batch window."
        ),
        expected_action_type="noop",
        expected_action_target_kind="",
    ),
    Scenario(
        id="retry_storm",
        name="5xx spike caused by upstream retries",
        severity="page",
        alert_name="HighErrorRate",
        alert_description="orders 5xx rate up; request volume from checkout-svc 20x baseline",
        fixture="fx_retry_storm",
        expected_root_cause=(
            "The checkout-svc upstream is hammering orders with retries of the "
            "same idempotency keys. The bug is in checkout-svc's retry policy, "
            "not in orders."
        ),
        expected_remediation="Page the checkout team to fix retry policy.",
        expected_action_type="page_team",
        expected_action_target_kind="team",
    ),
    Scenario(
        id="partial_outage_redundant",
        name="One DB replica unhealthy — system is fine",
        severity="ticket",
        alert_name="ReplicaUnhealthy",
        alert_description="1/3 db replicas unhealthy; orders 5xx and latency normal",
        fixture="fx_partial_outage_redundant",
        expected_root_cause=(
            "One of three DB replicas is unhealthy but the other two are serving "
            "all reads. No customer-visible impact."
        ),
        expected_remediation=(
            "No urgent action. File a ticket for the platform team to replace "
            "the replica during business hours."
        ),
        expected_action_type="noop",
        expected_action_target_kind="",
    ),
]


def by_id(scenario_id: str) -> Scenario:
    for s in SCENARIOS:
        if s.id == scenario_id:
            return s
    raise KeyError(f"unknown scenario: {scenario_id}")
