"""Read-only FastAPI application for the Finch research and paper-trading portal."""

from __future__ import annotations

from fastapi import FastAPI, Query, Request
from fastapi.responses import JSONResponse

from web.backend.db_queries import (
    get_data_payload,
    get_health_payload,
    get_jobs_payload,
    get_models_payload,
    get_overview_payload,
    get_rankings_payload,
    get_sandbox_payload,
)
from web.backend.gcp_metrics import get_cloud_billing_payload, get_cloud_resources_payload

app = FastAPI(
    title="Finch Monitoring API",
    version="0.2.0",
    description="Strictly read-only monitoring interface for Finch paper-trading research.",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)


@app.middleware("http")
async def enforce_read_only_verbs(request: Request, call_next):
    if request.method not in {"GET", "HEAD"}:
        return JSONResponse(
            status_code=405,
            content={"detail": "Method not allowed: Finch public endpoint is read-only (GET/HEAD only)."},
        )
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/api/health")
def api_health() -> dict:
    return get_health_payload()


@app.get("/api/overview")
def api_overview() -> dict:
    return get_overview_payload()


@app.get("/api/data")
def api_data() -> dict:
    return get_data_payload()


@app.get("/api/models")
def api_models() -> dict:
    return get_models_payload()


@app.get("/api/rankings")
def api_rankings() -> dict:
    return get_rankings_payload()


@app.get("/api/sandbox")
def api_sandbox(
    limit: int = Query(default=100, ge=1, le=500),
    model_name: str | None = Query(default=None),
    trade_date: str | None = Query(default=None),
    only_champion: bool = Query(default=False),
) -> dict:
    return get_sandbox_payload(
        limit=limit,
        model_name=model_name,
        trade_date=trade_date,
        only_champion=only_champion,
    )


@app.get("/api/jobs")
def api_jobs(limit: int = Query(default=80, ge=1, le=300)) -> dict:
    return get_jobs_payload(limit=limit)


@app.get("/api/cloud/resources")
def api_cloud_resources() -> dict:
    return get_cloud_resources_payload()


@app.get("/api/cloud/billing")
def api_cloud_billing() -> dict:
    return get_cloud_billing_payload()
