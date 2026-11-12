# ADMARKET AI Media Buyer v1.6.0

## New
- Platform profile registry for suppliers.
- Immutable historical audience metric snapshots.
- Transparent anomaly heuristics and deduplicated manual-review signals.
- Admin verification workflow and JSON export.
- Supplier dashboard forms for platform profile and metric submissions.

## Important limitations
Metrics are entered by suppliers. ADMARKET does not scrape or authenticate social-platform APIs in this release. HTTPS links are stored as references only; link reachability and ownership are not proven. A verified badge means an administrator performed an internal review and must not be represented as platform-issued verification, KYC or a fraud-free guarantee. Anomaly flags are not proof of manipulation and never trigger automatic account sanctions.

## Upgrade
Stop the app and back up `data/admarket.db`. Replace application files with this release while preserving the existing `data` directory. Startup creates new tables and indexes additively. Metric snapshots are immutable; new measurements create new rows.

## Tests
Run `PYTHONPATH=. python -m unittest discover -s tests -v`.
