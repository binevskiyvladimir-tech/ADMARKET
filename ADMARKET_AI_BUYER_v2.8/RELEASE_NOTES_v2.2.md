# ADMARKET v2.2.0 — AI Media Planning

- Added advertiser-owned saved media plan snapshots.
- Added `/media-planner` workspace for creating, reviewing and saving plans.
- Planner uses the current approved/demo inventory and the existing deterministic budget allocator.
- AI explanation is generated when OpenAI is configured; safe fallback remains available without a key.
- Added owner-scoped attach flow to copy a saved plan snapshot into an owned campaign.
- Attaching a plan does not activate a campaign, reserve inventory, charge money, or guarantee results.
- Additive SQLite migration creates `media_plans`; existing campaign and financial records remain intact.
