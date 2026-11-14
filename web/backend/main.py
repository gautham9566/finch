"""Read-only FastAPI application for the Finch research and paper-trading portal."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

from fastapi import Body, FastAPI, Query, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from web.backend.actions import (
    get_paper_options_payload,
    get_training_status,
    trigger_model_training,
    trigger_reset_trades,
    trigger_simulation,
    update_paper_options,
)
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
    is_action_post = request.method == "POST" and request.url.path.startswith("/api/actions/")
    if request.method not in {"GET", "HEAD"} and not is_action_post:
        return JSONResponse(
            status_code=405,
            content={"detail": "Method not allowed: Finch public monitoring endpoints are GET/HEAD only."},
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


@app.get("/api/actions/paper-options")
def api_get_paper_options() -> dict:
    return get_paper_options_payload()


@app.post("/api/actions/paper-options")
def api_post_paper_options(payload: Annotated[dict[str, Any] | None, Body()] = None) -> dict:
    return update_paper_options(payload or {})


@app.post("/api/actions/reset-trades")
def api_post_reset_trades() -> dict:
    return trigger_reset_trades()


@app.post("/api/actions/simulate")
def api_post_simulate(payload: Annotated[dict[str, Any] | None, Body()] = None) -> dict:
    return trigger_simulation(payload or {})


@app.post("/api/actions/train")
def api_post_train(payload: Annotated[dict[str, Any] | None, Body()] = None) -> dict:
    return trigger_model_training(payload or {})


@app.get("/api/actions/train/status")
def api_get_train_status() -> dict:
    return get_training_status()


_FRONTEND_DIR = Path(__file__).resolve().parents[1] / "frontend"
if _FRONTEND_DIR.is_dir():
    app.mount("/", StaticFiles(directory=str(_FRONTEND_DIR), html=True), name="frontend")
