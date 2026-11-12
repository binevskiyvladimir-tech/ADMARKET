# ADMARKET v1.5 release notes

## What's new
- Explainable supplier quality index (40% ratings, 30% SLA, 20% active complaints, 10% internal verification).
- Historical quality snapshots and administrator JSON report at `/admin/quality.json`.
- Manual anti-fraud queue for cross-supplier duplicate offers and unusual audience/price outliers.
- Admin can mark fraud signals reviewed or dismissed.

## Safety and limitations
- Signals are heuristics and are not proof of fraud.
- No automatic rejection, hidden ranking penalty, suspension or ban is performed.
- No independent verification of audience statistics is performed.
- Real payments, escrow custody and payouts remain disabled; payment functionality is sandbox-only.

## Upgrade
1. Stop the running server.
2. Back up the entire existing `data` directory, especially `admarket.db` and `dispute_evidence`.
3. Replace application files with this release. Preserve your existing `data` directory; do not overwrite it with the archive's empty placeholders.
4. Install requirements and start the application. New v1.5 tables are created additively.
5. Check `/health`, then open `/admin` and inspect the quality and anti-fraud sections.

The archive intentionally does not ship a development SQLite database or evidence files. Existing user data must be retained from the deployment backup.
