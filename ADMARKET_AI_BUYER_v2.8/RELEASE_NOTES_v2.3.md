# ADMARKET AI Media Buyer v2.3.0

## Campaign Analytics

- Daily campaign metric snapshots: spend, impressions, clicks, conversions, optional revenue.
- Derived CTR, CPC, CPM, conversion rate, CPA and ROI. Missing/zero denominators are displayed as unavailable. ROI requires revenue for every recorded day and non-zero spend.
- Advertiser ownership enforced server-side for view, write and CSV export.
- One snapshot per campaign/date; re-submitting a date updates that snapshot.
- CSV export for recorded daily snapshots.
- Additive migration; existing campaigns, media plans, payments and records are preserved.

## Data integrity and limitations

Metrics are manually entered by the advertiser. They are not fetched from ad networks or independently verified. The product does not fabricate impressions, clicks, conversions or revenue. Campaign analytics does not activate campaigns, reserve inventory, or move real money. Payments remain sandbox-only. Admin access restriction remains unchanged.

## Verification

- `python -m py_compile app/*.py`
- `PYTHONWARNINGS=ignore PYTHONPATH=. python -m unittest discover -s tests -q` — 44 tests passed.
- `PYTHONWARNINGS=ignore PYTHONPATH=. python tests/http_smoke.py` — integration passed.
