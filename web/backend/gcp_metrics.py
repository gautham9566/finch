"""GCP Cloud Monitoring, GCS Storage, Container Health, and Billing readers (read-only)."""

from __future__ import annotations

import os
import shutil
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import requests

from libs.common.config import DATA_DIR, get_gcp_settings
from libs.common.logging import get_logger
from libs.common.storage import get_bucket_stats
from pipelines.data.intraday import BrokerIntradayAdapter
from web.backend.db_queries import get_health_payload

IST = ZoneInfo("Asia/Kolkata")
log = get_logger("finch.web.gcp_metrics")

# GCP asia-south1 (Mumbai) public list-rate approximations (INR per month @ 730h, 1 USD ~= 84 INR)
MUMBAI_MONTHLY_UNIT_ESTIMATE_INR = {
    "Compute Engine e2-standard-4 (4 vCPU, 16 GB)": 10150.0,
    "Boot Persistent Disk (100 GB pd-standard)": 360.0,
    "Static External IPv4 + Egress allowance": 310.0,
    "Cloud Storage Standard (asia-south1)": 120.0,
    "Secret Manager + Cloud Monitoring free/base tier": 30.0,
}


def _read_local_host_stats() -> dict[str, Any]:
    """Read container host memory and disk utilization directly without external privileges."""
    mem_total_kb = 0
    mem_available_kb = 0
    try:
        for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
            if line.startswith("MemTotal:"):
                mem_total_kb = int(line.split()[1])
            elif line.startswith("MemAvailable:"):
                mem_available_kb = int(line.split()[1])
    except Exception:  # noqa: BLE001
        pass

    mem_used_pct = round((1.0 - (mem_available_kb / mem_total_kb)) * 100.0, 2) if mem_total_kb > 0 else None

    disk_target = DATA_DIR if DATA_DIR.exists() else Path("/")
    disk = shutil.disk_usage(disk_target)
    disk_used_pct = round((disk.used / disk.total) * 100.0, 2) if disk.total > 0 else None

    load_1m = None
    try:
        loadavg_fn = getattr(os, "getloadavg", None)
        if loadavg_fn is not None:
            load_1m = round(float(loadavg_fn()[0]), 2)
    except Exception:  # noqa: BLE001
        pass

    return {
        "mem_total_gb": round(mem_total_kb / (1024 * 1024), 2) if mem_total_kb else None,
        "mem_used_pct": mem_used_pct,
        "disk_total_gb": round(disk.total / (1024**3), 2),
        "disk_used_gb": round(disk.used / (1024**3), 2),
        "disk_used_pct": disk_used_pct,
        "load_1m": load_1m,
    }


def _query_cloud_monitoring_cpu(project_id: str, vm_name: str) -> dict[str, Any]:
    """Fetch latest VM CPU utilization and network bytes via GCP Cloud Monitoring."""
    try:
        from google.cloud import monitoring_v3  # noqa: PLC0415

        client = monitoring_v3.MetricServiceClient()
        now = time.time()
        interval = monitoring_v3.TimeInterval(
            {
                "end_time": {"seconds": int(now)},
                "start_time": {"seconds": int(now - 3600)},
            }
        )
        flt = (
            'metric.type = "compute.googleapis.com/instance/cpu/utilization" '
            f'AND resource.labels.instance_name = "{vm_name}"'
        )
        # Some zones expose instance_id only; fall back to project-wide GCE metric filter if empty
        results = list(
            client.list_time_series(
                request={
                    "name": f"projects/{project_id}",
                    "filter": 'metric.type = "compute.googleapis.com/instance/cpu/utilization"',
                    "interval": interval,
                    "view": monitoring_v3.ListTimeSeriesRequest.TimeSeriesView.FULL,
                },
                timeout=6.0,
            )
        )
        points: list[dict[str, Any]] = []
        matched_series = None
        for ts in results:
            inst_name = ts.resource.labels.get("instance_name", "")
            if vm_name in inst_name or not matched_series:
                matched_series = ts
                if vm_name in inst_name:
                    break
        if matched_series is not None:
            for pt in reversed(matched_series.points):
                val = pt.value.double_value * 100.0
                raw_end = pt.interval.end_time
                ts_raw = raw_end.ToDatetime() if hasattr(raw_end, "ToDatetime") else raw_end
                if ts_raw.tzinfo is None:
                    ts_raw = ts_raw.replace(tzinfo=UTC)
                ts_dt = ts_raw.astimezone(IST)
                points.append({"ts": ts_dt.isoformat(), "cpu_pct": round(val, 2)})

        return {
            "source": "gcp_cloud_monitoring",
            "available": bool(points),
            "latest_cpu_pct": points[-1]["cpu_pct"] if points else None,
            "series": points[-24:],
            "filter_note": flt if not points else None,
        }
    except Exception as exc:  # noqa: BLE001
        log.info(f"Cloud Monitoring unavailable or not running in GCP VM: {exc}")
        return {
            "source": "unavailable",
            "available": False,
            "latest_cpu_pct": None,
            "series": [],
            "error": str(exc),
        }


def _check_internal_services() -> list[dict[str, Any]]:
    settings = get_gcp_settings()
    health = get_health_payload()
    mlflow_uri = settings["mlflow_uri"].rstrip("/")

    mlflow_ok = False
    try:
        r = requests.get(f"{mlflow_uri}/health", timeout=2.5)
        mlflow_ok = r.status_code == 200
    except Exception:  # noqa: BLE001
        mlflow_ok = False

    intraday_ok = BrokerIntradayAdapter().is_configured()
    return [
        {
            "service": "postgres",
            "status": "healthy" if health["db_connected"] else "unhealthy",
            "detail": "PostgreSQL 16 (private loopback + Compose net)",
        },
        {
            "service": "mlflow",
            "status": "healthy" if mlflow_ok else "unreachable",
            "detail": mlflow_uri,
        },
        {
            "service": "web",
            "status": "healthy",
            "detail": "FastAPI read-only GET API",
        },
        {
            "service": "scheduler",
            "status": "active" if health["latest_job"] else "idle_no_runs_yet",
            "detail": (
                f"Last: {health['latest_job']['job_name']} ({health['latest_job']['status']})"
                if health["latest_job"]
                else "IST APScheduler waiting for first scheduled window"
            ),
        },
        {
            "service": "intraday_broker_adapter",
            "status": "configured" if intraday_ok else "not_configured",
            "detail": "Secret finch-intraday-api-key" if intraday_ok else "TODO: add Secret Manager version",
        },
    ]


def get_cloud_resources_payload() -> dict[str, Any]:
    settings = get_gcp_settings()
    bucket_stats = get_bucket_stats()
    host_stats = _read_local_host_stats()
    cpu_monitor = _query_cloud_monitoring_cpu(settings["project_id"], settings["vm_name"])
    services = _check_internal_services()

    parquet_bytes = 0
    parquet_count = 0
    if DATA_DIR.exists():
        for p in DATA_DIR.rglob("*.parquet"):
            parquet_count += 1
            parquet_bytes += p.stat().st_size

    db_bytes = None
    try:
        from web.backend.db_queries import _one  # noqa: PLC0415

        res = _one("SELECT pg_database_size(current_database()) AS db_bytes")
        db_bytes = int(res["db_bytes"]) if res else None
    except Exception:  # noqa: BLE001
        pass

    return {
        "gcp": settings,
        "bucket": {
            "name": settings["gcs_bucket"],
            "object_count": bucket_stats["object_count"],
            "total_mb": round(bucket_stats["total_bytes"] / (1024 * 1024), 2),
        },
        "local_storage": {
            "parquet_files": parquet_count,
            "parquet_mb": round(parquet_bytes / (1024 * 1024), 2),
            "postgres_mb": round(db_bytes / (1024 * 1024), 2) if db_bytes is not None else None,
        },
        "vm_host": host_stats,
        "cloud_monitoring": cpu_monitor,
        "services": services,
        "checked_at_ist": datetime.now(IST).isoformat(),
    }


def _try_bigquery_billing_export(project_id: str) -> dict[str, Any] | None:
    """Query BigQuery billing export dataset if linked and populated; otherwise return None."""
    dataset_name = os.getenv("FINCH_BILLING_DATASET", "billing_export")
    try:
        from google.cloud import bigquery  # noqa: PLC0415

        client = bigquery.Client(project=project_id)
        tables = list(client.list_tables(f"{project_id}.{dataset_name}", timeout=5.0))
        export_tables = [t.table_id for t in tables if t.table_id.startswith("gcp_billing_export_v1")]
        if not export_tables:
            return None

        table_ref = f"`{project_id}.{dataset_name}.{export_tables[0]}`"
        sql = f"""
            SELECT
                DATE(usage_start_time, 'Asia/Kolkata') AS usage_date,
                service.description AS service_name,
                currency,
                ROUND(SUM(cost) + SUM(IFNULL((SELECT SUM(c.amount) FROM UNNEST(credits) c), 0)), 2) AS net_cost
            FROM {table_ref}
            WHERE usage_start_time >= TIMESTAMP_TRUNC(CURRENT_TIMESTAMP(), MONTH, 'Asia/Kolkata')
            GROUP BY usage_date, service_name, currency
            ORDER BY usage_date ASC, net_cost DESC
        """
        rows = [dict(r) for r in client.query(sql, timeout=8.0).result()]
        if not rows:
            return {"status": "export_table_empty", "rows": []}
        return {"status": "ready", "table": export_tables[0], "rows": rows}
    except Exception as exc:  # noqa: BLE001
        log.info(f"BigQuery billing export not active yet: {exc}")
        return None


def get_cloud_billing_payload() -> dict[str, Any]:
    settings = get_gcp_settings()
    budget_inr = float(settings["budget_inr"])
    now_ist = datetime.now(IST)
    day_of_month = now_ist.day

    bq_data = _try_bigquery_billing_export(settings["project_id"])
    if bq_data and bq_data["status"] == "ready":
        rows = bq_data["rows"]
        currency = rows[0].get("currency", "INR")
        total_mtd = round(sum(float(r["net_cost"]) for r in rows), 2)
        by_service: dict[str, float] = {}
        by_day: dict[str, float] = {}
        for r in rows:
            svc = str(r["service_name"])
            d_str = r["usage_date"].isoformat()
            cost = float(r["net_cost"])
            by_service[svc] = round(by_service.get(svc, 0.0) + cost, 2)
            by_day[d_str] = round(by_day.get(d_str, 0.0) + cost, 2)

        return {
            "source": "bigquery_export",
            "is_estimate": False,
            "export_status": "active",
            "currency": currency,
            "budget_inr": budget_inr,
            "budget_thresholds": [0.5, 0.9, 1.0],
            "mtd_total": total_mtd,
            "projected_month_total": round((total_mtd / max(day_of_month, 1)) * 30.0, 2),
            "service_breakdown": [
                {"service": k, "amount": v, "is_estimate": False}
                for k, v in sorted(by_service.items(), key=lambda kv: kv[1], reverse=True)
            ],
            "daily_series": [{"date": k, "amount": v, "is_estimate": False} for k, v in sorted(by_day.items())],
        }

    # Honest pending state + clearly labelled GCP resource price estimate
    monthly_total_est = sum(MUMBAI_MONTHLY_UNIT_ESTIMATE_INR.values())
    daily_rate_est = round(monthly_total_est / 30.0, 2)
    month_start = now_ist.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    daily_est_series = []
    for i in range(day_of_month):
        d = (month_start + timedelta(days=i)).date().isoformat()
        daily_est_series.append(
            {
                "date": d,
                "amount": round(daily_rate_est, 2),
                "is_estimate": True,
            }
        )
    mtd_est = round(daily_rate_est * day_of_month, 2)

    return {
        "source": "estimate",
        "is_estimate": True,
        "export_status": "billing export pending (data appears within about a day)",
        "currency": "INR",
        "budget_inr": budget_inr,
        "budget_thresholds": [0.5, 0.9, 1.0],
        "mtd_total": mtd_est,
        "projected_month_total": round(monthly_total_est, 2),
        "service_breakdown": [
            {
                "service": svc,
                "amount": round((amt / 30.0) * day_of_month, 2),
                "monthly_estimate": amt,
                "is_estimate": True,
            }
            for svc, amt in MUMBAI_MONTHLY_UNIT_ESTIMATE_INR.items()
        ],
        "daily_series": daily_est_series,
        "setup_note": (
            f"Enable BigQuery Billing Export to dataset '{settings['project_id']}.billing_export' "
            "in GCP Billing -> Billing export to replace this estimate with live invoice rows."
        ),
    }
