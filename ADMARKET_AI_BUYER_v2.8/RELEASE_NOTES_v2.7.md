# ADMARKET v2.7.0 — Partner Analytics

## Added
- Supplier partner dashboard now shows lifetime, 7-day and 30-day referral totals.
- Shows a 14-day daily referral activity table and lifetime click-to-registration conversion rate.
- Supplier-only CSV export for 7, 30, 90 or 365 days, grouped by UTC date.
- CSV exports contain aggregated counts only; they do not expose referred users' emails, IDs, IP addresses or browser identifiers.
- Admin Partner Promotion queue shows referral conversion alongside clicks and advertiser registrations.
- CSV export uses authenticated supplier ownership, bounded period choices, `no-store` cache policy and UTF-8 BOM for spreadsheet compatibility.

## Compatibility and limitations
- No database migration is required; v2.7 reads the existing v2.6 referral event table.
- Existing referral attribution, manual promotion review and supplier publishing gate are preserved.
- Click counts remain session-deduplicated, not a measure of unique human visitors or verified ad impressions.
- Registrations are attributed advertiser accounts, not paying customers or completed transactions.
- No commission balances or payouts are enabled.
- Payment mode remains sandbox-only; existing administrator allowlist remains unchanged.

## Upgrade
1. Back up `data/` and `.env`.
2. Replace application code with this release while preserving `data/` and `.env`.
3. Install dependencies with `pip install -r requirements.txt`.
4. Restart the app. No migration is needed for this release.
