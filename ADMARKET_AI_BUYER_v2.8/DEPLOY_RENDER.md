# Render deployment

This project is configured for a Render Docker web service.

- Build: handled by `Dockerfile`.
- Start: `uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-10000}`.
- Persistent SQLite data: `/srv/app/data` (Render persistent disk).
- Payments remain sandbox-only (`PAYMENT_MODE=test`, `PAYMENT_PROVIDER=sandbox`).

Before launch, set `ADMIN_EMAIL` and a unique `ADMIN_PASSWORD` (at least 12 characters) in Render. Add `OPENAI_API_KEY` only if AI provider access is desired. Do not commit `.env` or real credentials.

The Render blueprint uses a Starter service with a 1 GB persistent disk; this is not a free-tier deployment.
