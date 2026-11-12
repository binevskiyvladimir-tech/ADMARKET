# ADMARKET AI Media Buyer v2.8.0 — Partner Rewards (sandbox)

## Added
- Fixed per-qualified-deal partner bonus policy in admin settings; default is 0 ₽ (disabled).
- Immutable/idempotent partner reward record keyed uniquely by sandbox payment intent.
- A reward is eligible only when the advertiser was attributed to the supplier, the supplier promotion is approved, the placement is completed, the sandbox payment succeeded, and the admin manually released the simulated escrow.
- Supplier dashboard shows pending, approved, and sandbox-marked totals plus reward history.
- Admin can approve, reverse, or mark an approved reward as `paid_sandbox`; no money is transferred.
- Conservative anti-fraud flag for three or more qualifying rewards for the same referred advertiser within 24 hours. Flags require human review and do not auto-ban.

## Safety and limitations
- No real payment provider, bank transfer, payout, or commission settlement is implemented.
- The default reward amount is 0 ₽; admin must explicitly configure a test amount.
- Only sandbox payment intents and simulated escrow releases can qualify.
- A sandbox-paid state is an internal test marker, not proof of a real-world payment.
- Existing v2.7 database data and migrations are preserved; the v2.8 migration is additive and idempotent.
