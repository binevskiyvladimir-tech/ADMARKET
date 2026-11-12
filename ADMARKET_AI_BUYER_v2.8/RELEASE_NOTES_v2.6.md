# ADMARKET v2.6.0 — Partner Promotion

## Added
- Supplier registration now collects an HTTPS URL to the supplier's publication containing an ADMARKET promotion.
- New supplier promotion submissions are pending until an administrator manually reviews the actual publication.
- Supplier offer creation is server-side blocked until the promotion placement is approved.
- Each supplier receives a personal referral URL at `/r/{code}`.
- Referral click counts are deduplicated per browser session; only aggregate click events are stored (no IP address or user-agent).
- Advertiser registrations reached through a partner URL are attributed to that supplier using first-touch session attribution.
- Supplier dashboard shows promotion status, referral URL, click count and referred advertiser registrations.
- Admin panel includes a Partner Promotion review queue and manual approve/reject controls.
- Existing supplier accounts receive referral codes during the additive database migration and can submit a promotion URL from their dashboard.

## Safety and limitations
- Publication verification is manual; no crawler or automatic proof claim is made.
- A counted registration means a new advertiser account, not a paid customer or completed transaction.
- No referral commissions, cash balances or payouts are created in this release. Referral metrics are informational only.
- Existing database tables and records are preserved; new columns, tables and indexes are additive.
- Existing admin authorization remains restricted to the configured allowlisted email AND the `admin` role.
- Payment functionality remains sandbox-only.

## Upgrade
1. Back up the complete `data` directory and `.env`.
2. Install dependencies from `requirements.txt`.
3. Deploy the new code and start the app; `init_db()` applies additive migrations.
4. Existing suppliers can add their publication URL under Partner Promotion in the dashboard.
5. Admins review the publication under `/admin` → Partner Promotion.
