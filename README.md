# checkout-service-demo

The "production" service used by the [OnCall Forge](../oncall-forge) hackathon demo.
A minimal FastAPI checkout endpoint that the OnCall Forge agent investigates,
fixes, and redeploys.

## Run locally

```bash
python -m venv .venv && . .venv/Scripts/activate   # or source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

- `POST /checkout` — `{"cart_total": 100.0, "code": "SAVE10"}`
- `GET /health`
- `GET /status` — live HTML dashboard (auto-refreshes every 2s)
- `GET /metrics` — JSON error-rate window, read by `oncall-forge`'s observability MCP server

## Known-good tag

`demo-good` marks the last known-healthy commit on `main`. `reset_demo.sh` in
`oncall-forge/ops` force-resets `main` back to this tag between demo takes.
