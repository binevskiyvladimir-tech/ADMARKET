# ADMARKET v2.1.0 — Campaign Control

## Delivered
- Advertiser campaign creation with budget, objective, geography, channels, audience and dates.
- Optional link to an advertiser-owned brief.
- Campaign lifecycle: draft, active, paused, completed, archived.
- Status transition history in campaign_events.
- Ownership checks and CSRF protection on every campaign mutation.
- Responsive campaign workspace on advertiser dashboard.

## Data safety
The release adds `campaigns` and `campaign_events` with `CREATE TABLE IF NOT EXISTS`. It does not drop or rewrite existing data. Back up `data/` before upgrading. Payment processing remains sandbox-only. API credentials and live database files are excluded.

## Verification
Run `python -m unittest discover -s tests -q` and `PYTHONPATH=. python tests/http_smoke.py`.
