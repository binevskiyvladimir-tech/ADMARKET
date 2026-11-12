## 2.6.0 — Partner Promotion

- Supplier registration collects a public HTTPS link to an ADMARKET promotion.
- Manual admin review gates supplier offer publishing until the placement is approved.
- Per-supplier referral URLs track session-deduplicated visits and advertiser registrations.
- Supplier dashboard and admin moderation queue added.
- Additive SQLite migration; existing data and role boundaries preserved.
- Referral counts are not revenue, commissions or payouts.

# ADMARKET changelog

## 0.9.0 — 2026-11-11
- Added sandbox-only payment intents and a test checkout flow; no live PSP integration or real payment execution.
- Added a balanced ledger, user transaction history and sandbox balances/saldos.
- Preserved accepted-term commission snapshots; subsequent commission changes affect only new accepted terms.
- Added administrator-only full test refund with reversing ledger entries and duplicate-refund protection.
- Added supplier verification status and admin decision history.
- Added bilateral reviews for completed placements, rating 1–5 and uniqueness per reviewer/placement.
- Added risk-event signals and admin triage.
- Added additive SQLite migration and tests for migration idempotency, balanced postings/reversals, verification and duplicate-review constraints.
- Retained v0.8 account registration, email verification, password reset, server-side role checks, AI planning, marketplace, placement and admin functions.

## 0.8.0
- Email verification and password reset tokens, request throttling and editable commission setting.


## 1.0.0 — 2026-11-11

- Added additive migration for escrow state, webhook idempotency, escrow event history and moderation review records.
- Successful sandbox payments now enter a simulated hold; admin release is permitted only after placement completion.
- Added HMAC-SHA256 signed sandbox webhook endpoint with replay/idempotency checks.
- Refunds now reverse every ledger posting for a payment, including release postings.
- Added heuristic pre-moderation with human review; no automatic approval, rejection or account suspension.
- Supplier catalogue now requires both offer approval and internal supplier verification.
- Added role-scoped campaign analytics page.
- Added v1.0 migration, webhook, moderation and HTTP integration tests.
- Live payments, real escrow and payouts remain intentionally disabled.


## 1.1.0 — 2026-11-11
- Added read-only admin financial reconciliation endpoint (`/admin/reconciliation`) for ledger transaction balance, escrow clearing net and refund-record consistency.
- Added SQLite `BEGIN IMMEDIATE` serialization around sandbox webhook processing, escrow release and refund to reduce duplicate/concurrent state-transition races.
- Added reconciliation regression tests; no schema migration required.


## v1.2.0
- Добавлены споры по рекламным размещениям, ссылки и приватные файлы-доказательства.
- Добавлены административное рассмотрение, уведомления и аудит решений.
- Выпуск sandbox-эскроу блокируется на время активного спора; после решения проверяется допустимость выпуска.
- Добавлен уникальный DB guard от повторного выпуска и сигнал о повторном использовании файла.


## v1.3.0
- Добавлена запись подтверждения публикации с неизменяемыми полями доказательства для каждого размещения.
- Поставщик должен отправить HTTPS-ссылку на опубликованный материал; пустая/HTTP-ссылка не принимается.
- Рекламодатель может подтвердить завершение только после сохранения доказательства публикации.
- Факт отправки и принятия подтверждения фиксируется в журнале действий; ссылка видна обеим сторонам.
- Additive SQLite migration; старые размещения и исторические данные не удаляются.
- Реальные платежи и выплаты по-прежнему отключены.


## v1.4.0 — supplier quality & SLA
- Added 72-hour supplier delivery SLA records on placement confirmation; publication marks SLA as met.
- Added admin-triggered overdue SLA scan with risk-event logging.
- Added advertiser complaints with categories, status workflow, resolution notes and immutable event history.
- Added supplier reputation summary and admin view of average ratings, review counts and active complaints.
- Existing payment sandbox, dispute evidence, publication receipts and earlier migrations retained.
- Real payments remain disabled; no automatic supplier suspension or rating penalties.


## v1.5.0 — supplier quality index and anti-fraud triage
- Added explainable supplier quality score (ratings 40%, SLA 30%, active complaints 20%, internal verification 10%).
- Added append-only quality snapshots and a read-only admin JSON report.
- Added conservative cross-supplier duplicate-listing and outlier price/audience signals with deduplication.
- All anti-fraud signals are review-only; no automatic rejection, ranking penalty, suspension or ban.
- Added admin review/dismissal workflow and migration/tests.
- Sandbox-only payments and prior dispute/evidence/SLA/complaint functionality retained.


## v1.6.0 — platform account registry and audience-trust triage

- Supplier-owned public platform profile registry with duplicate protection and HTTPS-only profile links.
- Append-only, immutable metric snapshots (followers, average views/reach, engagement rate) with input bounds.
- Explainable audience-trust assessments compare the latest two snapshots and flag unusual follower growth, view/follower ratios, engagement rates and reach.
- Admin-only manual platform verification, signal review/dismissal, and JSON report.
- Supplier and admin UI for submitting profiles, periodic metrics and reviewing signals.
- All anomaly findings are human-review signals; metrics remain supplier-submitted and are not independently verified through platform APIs. No automatic bans, ranking suppression or fraud verdicts.
- Additive SQLite migrations; existing tables and records are preserved. Real payment processing remains disabled.

## v1.7.0
- Админ-панель и все защищённые admin API ограничены единственным allowlist email: `admin@example.com`. Проверка выполняется на сервере по роли и email; скрытие ссылки в интерфейсе не используется как механизм безопасности.
- Bootstrap администратора принимает только этот email; посторонний аккаунт с ролью `admin` получает 403.


## v1.8.0 — desktop workspace
- Добавлен отдельный desktop-композиционный блок на главной странице.
- Улучшены ширина контента, сетки карточек, кабинетов, таблиц и форм для мониторов.
- Сохранены адаптивные режимы для планшетов и мобильных устройств.
- Добавлены улучшения доступности: видимый keyboard focus и поддержка reduced motion.
- Серверные функции, база данных и ограничение админки по email сохранены.


## v1.9.0 — Dark Premium workspace redesign
- Unified premium dark visual system with graphite surfaces, lime accent, updated typography, navigation, buttons, forms, cards and responsive states.
- Reworked homepage messaging and AI buyer / media planning / marketplace visual hierarchy without changing existing endpoints or workflows.
- Added client-side marketplace filters by channel, geography, price ceiling and audience floor; sorting by price/audience and side-by-side comparison for up to three listings.
- Added role-aware workspace shortcuts, redesigned analytics KPI and finance panels, campaign register, wallet presentation and admin section navigation.
- Preserved the strict server-side admin allowlist, sandbox-only payment model, database schema, migrations and existing business workflows.
- Expanded UI regression checks for responsive design, navigation, analytics bindings and sandbox disclaimers.


## v2.0.0 — role-specific workspaces
- Added role-scoped advertiser and supplier KPI summaries from existing request, response, placement and offer records.
- Added contextual next-action cards with direct anchors to pending responses, opportunities, active placements, platform profiles and notifications.
- Added admin overview metrics; protected admin navigation remains conditional on the existing strict allowlist and backend authorization.
- No database schema changes or migrations; existing records, routes, role checks and sandbox payment model retained.
- Added regression coverage for workspace calculations, templates, version metadata and admin access controls.


## v2.1.0 — campaign management

- Added advertiser campaigns with budgets, objectives, targeting, channels and flight dates.
- Added owner-scoped campaign creation and status lifecycle with immutable event history.
- Added campaign workspace UI to advertiser dashboard, including responsive cards and status controls.
- Added additive SQLite schema migration; no existing tables or records are removed.
- Updated health/API version metadata and release tests.


## v2.2.0
- Saved advertiser media plans, AI explanation with fallback, and owner-checked campaign attachment.
- Additive `media_plans` migration; no existing records are deleted.


## v2.3.0

- Добавлена аддитивная таблица `campaign_metric_snapshots` для дневных показателей кампаний.
- Добавлены owner-scoped страницы аналитики, ручной ввод/обновление дневных записей и CSV-экспорт.
- Рассчитываются CTR, CPC, CPM, conversion rate, CPA и ROI; недостаточные данные не подменяются нулями или прогнозами.
- Добавлена проверка диапазонов и логики кликов/показов.
- Интеграционные проверки подтверждают изоляцию данных разных рекламодателей.
- Реальные рекламные API, импорт кабинетов и автоматическая верификация статистики не включены.

- **v2.4.0** — безопасный CSV-импорт дневной аналитики кампаний, строгая валидация, owner-scoped access и идемпотентное обновление по дате; прямые API-интеграции пока не активированы.

## 2.5.0 — Advertising Platform Integration Center
- Added advertiser integration hub and secure Yandex Direct OAuth 2.0 authorization-code scaffold.
- Added expiring one-time OAuth state, encrypted token persistence, account ownership and disconnect.
- VK Ads integration remains disabled pending official flow verification; API sync is not enabled for either provider.

## v2.7.0 — Partner Analytics
- Добавлены показатели партнёрской программы за 7 и 30 дней, конверсия за всё время и динамика событий за 14 дней.
- Поставщик может скачать агрегированный CSV-отчёт за 7, 30, 90 или 365 дней; экспорт доступен только владельцу-поставщику.
- CSV не раскрывает персональные данные привлечённых пользователей; добавлен `Cache-Control: no-store`.
- Администратор видит конверсию партнёрских ссылок в очереди Partner Promotion.
- Миграций нет: используются существующие события v2.6. Платежи и реферальные выплаты остаются отключёнными.


## v2.8.0
- Added configurable sandbox-only partner rewards, idempotent reward ledger, admin review/reversal/test-paid controls, supplier reward history, and manual-review anti-fraud flags.
