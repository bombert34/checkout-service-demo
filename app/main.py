"""Demo production checkout service and live incident dashboard."""

from __future__ import annotations

import logging
import json
import os
import subprocess
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from app.pricing import apply_discount
from app.telemetry import Telemetry
from app.log_records import recent_records

ROOT = Path(__file__).resolve().parent.parent
LOG_DIR = Path(os.environ.get('DOX_DATA_DIR', ROOT / 'logs'))
LOG_DIR.mkdir(parents=True, exist_ok=True)
APP_LOG = LOG_DIR / "app.log"
DEPLOYS_LOG = LOG_DIR / "deploys.log"

logger = logging.getLogger("checkout")
logger.setLevel(logging.INFO)
if not logger.handlers:
    handler = logging.FileHandler(APP_LOG)
    formatter = logging.Formatter("%(asctime)s %(levelname)s checkout: %(message)s", "%Y-%m-%dT%H:%M:%SZ")
    formatter.converter = time.gmtime
    handler.setFormatter(
        formatter
    )
    logger.addHandler(handler)

app = FastAPI(title="dox checkout service")
telemetry = Telemetry(LOG_DIR / 'telemetry.sqlite3')


class CheckoutRequest(BaseModel):
    cart_total: float
    code: str = ""


def _record(is_error: bool) -> None:
    telemetry.record(is_error)


def _error_rate(window_s: int) -> dict:
    return telemetry.metrics(window_s)


def _git(*args: str) -> str:
    try:
        return subprocess.check_output(
            ["git", *args], cwd=ROOT, text=True, stderr=subprocess.DEVNULL, timeout=2
        ).strip()
    except Exception:
        return "unavailable"


RUNNING_COMMIT = _git("rev-parse", "--short=12", "HEAD")
RUNNING_MESSAGE = _git("log", "-1", "--pretty=%s")


def _tail(path: Path, limit: int) -> list[str]:
    if not path.exists():
        return []
    return path.read_text(encoding="utf-8", errors="replace").splitlines()[-limit:]


@app.post("/checkout")
def checkout(req: CheckoutRequest):
    try:
        total = apply_discount(req.cart_total, req.code)
    except Exception:
        logger.error(
            "checkout failed for code=%r cart_total=%r\n%s",
            req.code,
            req.cart_total,
            traceback.format_exc(),
        )
        _record(is_error=True)
        raise HTTPException(status_code=500, detail="checkout failed")
    logger.info("checkout ok code=%r cart_total=%r total=%r", req.code, req.cart_total, total)
    _record(is_error=False)
    return {"total": total}


@app.get("/health")
def health():
    return {"status": "ok", "commit": RUNNING_COMMIT}


@app.get("/metrics")
def metrics(window_s: int = Query(default=60, ge=1, le=300)):
    return JSONResponse(_error_rate(window_s))


@app.get("/status-data")
def status_data():
    metrics_now = _error_rate(60)
    recent_errors = recent_records(APP_LOG, limit=5)
    deploys = _tail(DEPLOYS_LOG, 5)
    try:
        incident = json.loads((LOG_DIR / 'incident.json').read_text(encoding='utf-8'))
        # Only publish explicitly public progress; never expose monitor calls or arguments.
        incident = {key: incident[key] for key in ('stage', 'session_id', 'milestones', 'updated_at', 'monitor_error') if key in incident}
        incident['stale'] = time.time()-incident.get('updated_at', 0) > 20
    except (OSError, ValueError):
        incident = {'stage': 'Monitor not started', 'stale': True, 'milestones': []}
    return {
        **metrics_now,
        "commit": RUNNING_COMMIT,
        "commit_message": RUNNING_MESSAGE,
        "deploys": deploys,
        "recent_errors": recent_errors,
        "history": telemetry.history(),
        "incident": incident,
        "updated_at": datetime.now(timezone.utc).strftime("%H:%M:%S UTC"),
    }


@app.get("/status", response_class=HTMLResponse)
def status():
    return (Path(__file__).with_name("dashboard.html")).read_text(encoding="utf-8")
