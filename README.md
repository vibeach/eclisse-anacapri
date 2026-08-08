# Eclissi ad Anacapri · 12 Agosto 2026

RSVP portal for the eclipse-dinner event in Anacapri.

## Run locally

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
python app.py   # → http://localhost:8799
```

## Env

- `ECLISSE_ADMIN_KEY` — required for `/admin` and `/admin/export.csv`
- `ECLISSE_SECRET_KEY` — Flask session cookie signing
- `DATABASE_URL` — Postgres URL on Render; falls back to SQLite locally

## Endpoints

- `/` — public landing + RSVP form
- `/rsvp` (POST) — submit RSVP
- `/admin?key=...` — dashboard
- `/admin/export.csv?key=...` — CSV export
- `/healthz` — liveness
