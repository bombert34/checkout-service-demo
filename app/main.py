"""Demo production checkout service and live incident dashboard."""

from __future__ import annotations

import logging
import subprocess
import time
import traceback
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from app.pricing import apply_discount

ROOT = Path(__file__).resolve().parent.parent
LOG_DIR = ROOT / "logs"
LOG_DIR.mkdir(exist_ok=True)
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
WINDOW_S = 300
_events: deque[tuple[float, bool]] = deque()


class CheckoutRequest(BaseModel):
    cart_total: float
    code: str = ""


def _record(is_error: bool) -> None:
    now = time.time()
    _events.append((now, is_error))
    cutoff = now - WINDOW_S
    while _events and _events[0][0] < cutoff:
        _events.popleft()


def _error_rate(window_s: int) -> dict:
    now = time.time()
    cutoff = now - window_s
    recent = [event for event in _events if event[0] >= cutoff]
    total = len(recent)
    errors = sum(1 for _, is_error in recent if is_error)
    rate = errors / total if total else 0.0
    return {
        "window_s": window_s,
        "requests": total,
        "errors": errors,
        "error_rate": round(rate, 4),
    }


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
    return {"status": "ok"}


@app.get("/metrics")
def metrics(window_s: int = Query(default=60, ge=1, le=300)):
    return JSONResponse(_error_rate(window_s))


@app.get("/status-data")
def status_data():
    metrics_now = _error_rate(60)
    recent_errors = [line for line in _tail(APP_LOG, 300) if " ERROR " in line][-5:]
    deploys = _tail(DEPLOYS_LOG, 5)
    return {
        **metrics_now,
        "commit": RUNNING_COMMIT,
        "commit_message": RUNNING_MESSAGE,
        "deploys": deploys,
        "recent_errors": recent_errors,
        "updated_at": datetime.now(timezone.utc).strftime("%H:%M:%S UTC"),
    }


@app.get("/status", response_class=HTMLResponse)
def status():
    return (Path(__file__).with_name("dashboard.html")).read_text(encoding="utf-8")
