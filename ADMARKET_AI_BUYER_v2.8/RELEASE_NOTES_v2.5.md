# ADMARKET v2.5.0 — Advertising Platform Integration Center

## Added
- Advertiser-only `/integrations` workspace and `/api/integrations` status endpoint.
- Yandex Direct OAuth 2.0 authorization-code initiation and callback.
- Single-use, expiring OAuth state bound to the advertiser account.
- Access token encrypted with Fernet before persistence; token never rendered in HTML/API responses.
- Disconnect action deletes the stored encrypted token.
- VK Ads provider card intentionally remains disabled pending verification of the official OAuth/API access flow.

## Important limitations
- OAuth authorization is not equivalent to approved Yandex Direct API access. API access request and user agreement requirements remain with Yandex.
- v2.5 does not call Yandex Direct reporting endpoints and does not synchronize metrics. No advertising is created, edited, paused, or paid for.
- Production setup requires `YANDEX_DIRECT_CLIENT_ID`, `YANDEX_DIRECT_CLIENT_SECRET`, `YANDEX_DIRECT_REDIRECT_URI` (HTTPS), and a generated `INTEGRATION_ENCRYPTION_KEY`. Configure the exact same redirect URI in Yandex OAuth.
- Keep secrets out of source control. Back up the encryption key securely; losing it makes stored tokens unrecoverable.

## Upgrade
1. Back up the entire `data` directory and `.env`.
2. Install updated requirements.
3. Add the optional environment variables only after registering the Yandex OAuth app and obtaining API access.
4. Deploy behind HTTPS and set `YANDEX_DIRECT_REDIRECT_URI` to the exact public callback URL.
5. Start the app; additive tables are created by `init_db()`.

Existing accounts, campaigns, media plans, metrics, payment sandbox, disputes and admin restrictions are preserved.

## Official implementation references
- Yandex Direct API access and OAuth requirements: https://yandex.ru/support/direct/en/alternative-interfaces/api
- Yandex Direct authorization and access approval: https://yandex.com/dev/direct/doc/en/concepts/access
- Yandex OAuth authorization-code example: https://yandex.ru/dev/direct/doc/en/php5-file_get_contents-token
