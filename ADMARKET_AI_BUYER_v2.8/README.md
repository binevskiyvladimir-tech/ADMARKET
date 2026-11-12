# ADMARKET — AI Media Buyer v2.3.0

v1.5 сохраняет маркетплейс, роли, AI-планирование, согласование условий, финансовую историю и sandbox-платежи. Включает споры с доказательствами, SLA, жалобы, индекс качества поставщиков и антифрод-сигналы для ручной проверки. Согласование условий остаётся внутренней фиксацией действий, а не юридически оформленным договором.

## Новое в v1.5

- Индекс качества поставщиков с раскрытой формулой и историческими снимками.
- Антифрод-очередь: дублирующиеся объявления между аккаунтами и аномальные параметры.
- Все сигналы требуют ручного рассмотрения; автоматических банов и отказов нет.

## Новое в v1.4

- При подтверждении рекламного слота создаётся SLA-кейс со сроком 72 часа; отправка HTTPS-доказательства публикации закрывает SLA как выполненный.
- Администратор может запускать проверку просрочек; нарушения фиксируются как сигналы риска. Проверка не запускает автоматические штрафы или блокировки.
- Рекламодатель может отправить структурированную жалобу по размещению: сроки, качество, коммуникация, недостоверные сведения или другое.
- Жалобы имеют статусы «открыта / на рассмотрении / решена / отклонена», решение администратора и отдельную историю событий.
- В админ-панели отображаются средний рейтинг поставщика, число отзывов и активные жалобы; в кабинете поставщика — сводка рейтинга.
- Миграции аддитивные: таблицы и данные прошлых версий сохраняются. Рейтинг рассчитывается по отзывам после завершённых размещений; жалобы не снижают его автоматически.

- Поставщик при отметке публикации обязан указать HTTPS-ссылку на опубликованный материал.
- Ссылка, отправитель и время отправки сохраняются в отдельной таблице `placement_delivery_receipts`; поля доказательства защищены от изменения. Запись также фиксирует принятие рекламодателем.
- Рекламодатель не может завершить размещение без сохранённого подтверждения. Принятие подтверждения записывается в журнал действий.
- Миграция аддитивная: существующие записи не удаляются; исторические размещения без подтверждения остаются доступными, но для новых публикаций применяется обязательная ссылка.

## Новое в v1.2

- Стороны размещения могут открыть спор; на одно размещение допускается только один активный спор.
- К спору можно прикрепить ссылки и приватные файлы JPG/PNG/WEBP/PDF до 8 МБ. MIME проверяется по сигнатуре, файлы доступны только сторонам сделки и администратору.
- Администратор рассматривает спор, фиксирует мотивированное решение и уведомляет обе стороны; решение попадает в журнал действий.
- Активный спор блокирует выпуск sandbox-эскроу. После спора выпуск разрешён только при решении в пользу поставщика.
- Уникальный индекс БД защищает от повторного выпуска; повторное использование одного файла разными аккаунтами создаёт сигнал для ручной проверки.
- Сохранены финансовая сверка `/admin/reconciliation` и сериализация критических sandbox-операций из v1.1.

## Новое в v0.8

- Подтверждение email через одноразовую ссылку с ограниченным сроком действия.
- Восстановление пароля через одноразовую ссылку; сброс требует пароль длиной не менее 12 символов.
- Ограничение частоты запросов на восстановление и повторную отправку подтверждения.
- Администратор меняет расчётную комиссию в диапазоне 0–30%; новая ставка применяется к будущим принятым версиям условий, исторические финансовые снимки не переписываются.
- Миграции добавляют `email_verified`, токены, события безопасности и настройки; существующие аккаунты остаются подтверждёнными, новые требуют подтверждения.

## Сохранено из v0.7

- История расчётных финансовых оценок по каждой принятой версии условий.
- Фиксируемая формула комиссии: 12% от согласованной суммы; оценка поставщику = сумма минус комиссия; оценка бюджета рекламодателя = согласованная сумма.
- Отдельные представления финансовых данных: рекламодатель видит свои суммы и оценку комиссии; поставщик — свою оценку выплаты; администратор — агрегированную сводку.
- Администратор может выгрузить финансовые оценки в CSV.
- Additive-миграция `financial_estimates` и индексов; принятые версии v0.6 автоматически попадают в историю.
- Все суммы — расчётные оценки, а не баланс, начисление, задолженность, выручка или подтверждение платежа. Реальные платежи и удержание комиссии не реализованы.

## Сохранено из v0.6

- Поставщик может отправить версию условий: сроки/окно размещения, состав работ, цена, описание порядка оплаты, перенос/отмена и дополнительные условия.
- Каждая версия неизменяемо сохраняется в SQLite. Новая версия помечает предыдущую ожидающую версию как заменённую.
- Рекламодатель принимает или отклоняет конкретную версию. Согласованная версия хранит дату принятия; история предыдущих редакций сохраняется.
- Уведомления в кабинете о выборе поставщика, новых сообщениях и предложенных/отклонённых/принятых условиях. Есть отметка прочтения.
- Журнал действий по размещению.
- Серверная проверка роли и принадлежности размещения; CSRF для всех новых POST-форм.
- Автоматические additive-миграции SQLite: `placement_terms`, `notifications`, `activity_log` и индексы. Старые таблицы не удаляются.

## Запуск на Windows 10/11

Нужен Python 3.11+. Распакуй ZIP и открой PowerShell в корне проекта:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
py -m pip install -r requirements.txt
```

Если PowerShell блокирует активацию, выполняй `py -m pip ...` без активации окружения.

### Переменные окружения

```powershell
$env:SESSION_SECRET="длинная-случайная-строка-не-меньше-32-символов"
$env:ADMIN_EMAIL="admin@example.com"
$env:ADMIN_PASSWORD="уникальный-сложный-пароль-не-короче-12-символов"
```

Для email-писем (необязательно для локального теста; для реальной отправки настрой SMTP):

```powershell
$env:APP_BASE_URL="http://127.0.0.1:8000"
$env:SMTP_HOST="smtp.example.com"
$env:SMTP_PORT="587"
$env:SMTP_USER="mailer@example.com"
$env:SMTP_PASSWORD="пароль-приложения"
$env:SMTP_FROM="ADMARKET <mailer@example.com>"
$env:SMTP_STARTTLS="1"
```

Без SMTP ссылки выводятся в серверный лог только для локальной разработки. Не включай такой режим в публичном production.

Для AI (необязательно; ключ только на сервере):

```powershell
$env:OPENAI_API_KEY="твой-новый-api-ключ"
$env:OPENAI_MODEL="gpt-4.1-mini"
$env:OPENAI_BASE_URL="https://api.openai.com/v1"
```

Запуск:

```powershell
py -m uvicorn app.main:app --reload
```

Открой `http://127.0.0.1:8000`. Администратор создаётся при старте из `ADMIN_EMAIL` и `ADMIN_PASSWORD`. В v1.7 доступ к админ-панели и всем admin API разрешён только аккаунту `admin@example.com`; одного значения роли `admin` недостаточно.

## Проверка сценария

1. Создай рекламодателя и поставщика в отдельных браузерных сессиях.
2. Рекламодатель публикует бриф; поставщик отправляет отклик.
3. Рекламодатель выбирает поставщика.
4. Поставщик подтверждает доступность места и отправляет версию условий в карточке размещения.
5. Рекламодатель изучает версию и принимает или отклоняет её.
6. Проверь уведомления и журнал действий у обеих сторон.
7. Продолжи workflow v0.6: поставщик отмечает публикацию, рекламодатель — завершение.

## Основные маршруты

- `/` — AI-медиабайер и каталог.
- `/register`, `/login`, `/logout` — учётные записи; `/verify-email`, `/forgot-password`, `/reset-password`, `/resend-verification` — подтверждение и восстановление доступа.
- `/dashboard` — кабинет по роли, уведомления, заявки, размещения, переписка, версии условий.
- `/admin` — модерация, управление пользователями, финансовая сводка и настройка комиссии.
- `/admin/finance.csv` — CSV-экспорт расчётных оценок (только admin).
- `/supplier/placements/{id}/terms` — предложить новую версию условий.
- `/advertiser/placements/{id}/terms/{terms_id}/decision` — принять/отклонить версию.
- `/notifications/{id}/read`, `/notifications/read-all` — прочтение уведомлений.
- `/marketplace/placements/{id}/messages` — внутренние сообщения.
- `/api/inventory`, `/api/ai`, `/api/plan`, `/health` — API каталога, AI, плана и healthcheck.

## Важные ограничения и безопасность

- Принятие условий — внутренняя отметка в MVP, **не договор**, не электронная подпись и не юридическая экспертиза. Для реального запуска нужны юрист, корректная оферта/договорная модель, согласие на электронный документооборот и применимые требования права.
- Описание оплаты не инициирует и не подтверждает платёж. Нет эскроу, эквайринга, возвратов, резервирования места или автоматического расчёта комиссии.
- Статус публикации устанавливается участником; независимой проверки факта размещения нет.
- Демо-инвентарь и цены — демонстрационные, не реальные предложения. Модерация пользовательских предложений не подтверждает их статистику, права на площадку или доступность.
- Комиссия 12% — гипотеза, не является платёжной логикой.
- API-ключ хранится только в переменных окружения сервера. Не добавляй его в frontend, Git или ZIP.
- Перед production нужны HTTPS, `COOKIE_SECURE=1`, уникальный `SESSION_SECRET`, внешний rate limiting/WAF, SMTP с доменной аутентификацией SPF/DKIM/DMARC, MFA, безопасные резервные копии, мониторинг, независимый security review и политика хранения/удаления данных. Встроенный rate limit в MVP — базовая защита, не замена инфраструктурному ограничению запросов.
- Переписка и поля условий могут содержать персональные/коммерческие данные. Не используй MVP с реальными чувствительными данными до внедрения правовых оснований, сроков хранения и необходимых мер защиты.

## Обновление с v0.7

Останови сервер и сделай резервную копию `data/admarket.db`. Замени файлы приложения новой версией, сохранив каталог `data` и БД. При старте выполняются аддитивные миграции. Старые аккаунты автоматически считаются подтверждёнными, чтобы не блокировать существующих пользователей. Новые регистрации требуют подтверждения email. Для реальной отправки писем настрой SMTP. Новая комиссия применяется только к будущим согласованиям; старые финансовые снимки неизменны.

## Обновление с v0.6

Останови сервер. Сделай резервную копию `data/admarket.db`. Замени файлы приложения этой версии, сохранив каталог `data` и существующую БД. При первом старте новая таблица и индексы создадутся автоматически; принятые условия v0.6 будут добавлены в историю расчётных оценок. Миграция аддитивная; существующие записи не удаляются.

## v0.9 — sandbox finance, ledger and trust controls

This release preserves the v0.8 authentication, role checks, email token flows, admin area, placements, terms snapshots and AI planning features, and adds:

- sandbox-only payment intents (`PAYMENT_MODE=test`); the application does not call a real payment provider and never captures card/bank credentials;
- a balanced append-only ledger with user balances, platform and external-clearing entries;
- immutable commission-rate snapshots inherited from the accepted terms estimate; later admin rate changes affect only newly accepted terms;
- admin-only full reversal of successful sandbox postings (test refunds); no live refund API exists;
- supplier verification state and admin verification history;
- one review per completed placement per reviewer, ratings and review text;
- basic risk-event logging for test payment and refund actions, with admin triage.

### Payment safety and limitations

`PAYMENT_PROVIDER=sandbox` and `PAYMENT_MODE=test` are the only supported configuration in v0.9. The checkout page explicitly simulates success/failure. It is not connected to a PSP, does not move money, and its ledger is demonstrative only. Do not use sandbox balances as real funds, withdrawable amounts, revenue or liabilities. A production integration requires a separately reviewed provider adapter, signed webhook verification, idempotency keys, reconciliation, KYC/AML and legal/accounting review. None is claimed in this release.

SMTP remains separately configured using `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM`, `SMTP_STARTTLS`, and `APP_BASE_URL`. Without SMTP, account links are logged for local development only. Production deployment must set a strong random `SESSION_SECRET`, `COOKIE_SECURE=1` behind HTTPS, and configure SMTP.

### Upgrade

Back up `data/admarket.db` before upgrade. Startup migrations are additive and do not drop prior tables. The existing v0.7/v0.8 backfill for historic accepted agreements uses the legacy 12% default where earlier versions did not persist the actual historical rate; this limitation is retained transparently rather than guessed from current settings.

### Tests

Run `PYTHONPATH=. python -m unittest discover -s tests -v` for migration idempotency, preserved core tables, balanced sandbox ledger postings/reversals, supplier verification state and duplicate-review constraints.

### Trust controls scope

Supplier verification is an internal admin status workflow; v0.9 does not perform identity-document validation, company-registry checks or KYC. Reviews can be submitted by either party only after the placement reaches `completed`, with one review per reviewer for that placement. Risk events are rule-based audit signals, not an automated fraud verdict or a fraud-prevention guarantee.


## v1.0 — signed sandbox webhooks, escrow workflow and analytics

v1.0 is an additive upgrade from v0.9. Existing accounts, requests, responses, placements, terms, finance estimates, payment intents, reviews and ledger records are preserved.

- **Escrow simulation:** a successful sandbox payment debits the advertiser-side demo ledger and records the same amount in external sandbox clearing. Supplier and platform demo balances are not credited until an admin releases the hold. Release is allowed only after the placement is marked `completed`. The ledger remains balanced at each stage.
- **Signed webhooks:** `POST /webhooks/sandbox` validates an HMAC-SHA256 signature in `X-ADMARKET-SIGNATURE`. Event IDs are unique and idempotent; reusing an event ID with different payload is rejected. Only `payment.succeeded` and `payment.failed` are supported, and only in sandbox mode.
- **Test checkout:** the existing success/failure simulator now generates a signed internal sandbox event. It does not contact a payment processor.
- **Refunds:** admin test refunds reverse all ledger postings for the payment, including an escrow release if one occurred. A payment can be refunded only once.
- **Pre-moderation:** transparent heuristics flag suspicious wording, excessive external links, very long text and repeated characters for human review. They never auto-approve, auto-reject or suspend an account. Admin review state is stored separately from the supplier-offer approval state.
- **Supplier trust:** only admin-approved offers from active suppliers whose internal verification status is `verified` appear in the supplier catalogue. This status is not KYC and is not an independent audit.
- **Admin reconciliation:** `/admin/reconciliation` is a read-only JSON check for balanced ledger transactions, external sandbox clearing net versus escrow state, and missing refund records. It reports issues but never edits or repairs financial history.
- **Serialized state changes:** sandbox webhook processing, escrow release and refunds acquire a SQLite write reservation before reading state, reducing race conditions for duplicate concurrent operations.
- **Campaign analytics:** `/analytics` summarizes role-scoped requests, placements, completed work, review averages and sandbox payment/escrow amounts. It does not claim impressions, clicks, conversions, ROAS or verified marketing outcomes.
- **Mobile layout:** the analytics and moderation tables use the existing responsive table wrappers and responsive dashboard layout.

### Sandbox webhook configuration

Set a random secret of at least 24 characters (32+ recommended) in the server environment:

```powershell
$env:PAYMENT_MODE="test"
$env:PAYMENT_PROVIDER="sandbox"
$env:SANDBOX_WEBHOOK_SECRET="replace-with-a-long-random-secret"
```

For a local signed webhook, calculate HMAC-SHA256 over the exact raw JSON request body and send the lowercase hex digest in `X-ADMARKET-SIGNATURE`. Do not log or expose the secret. The local checkout simulator signs events internally.

### v1.0 limitations

There is still **no live PSP adapter, card/bank data collection, real payment, real escrow/custody, payout, or withdrawal**. Sandbox ledger amounts are not money or liabilities. The webhook endpoint is a sandbox protocol exercise, not a production provider integration. A real release requires a provider-specific adapter, provider signature format and key rotation, replay-window policy, external reconciliation, idempotent payout handling, KYC/AML, legal/accounting review and independent security testing. Heuristic moderation is not a substitute for trained moderation or fraud detection.

### v1.2 tests

```powershell
py -m compileall -q app tests
$env:PYTHONPATH="."
py -m unittest discover -s tests -v
py tests/http_smoke.py
```


## Обновление с v0.9 до v1.0

Останови сервер и обязательно скопируй `data/admarket.db` в резервную папку. Замени код приложения файлами v1.0, но сохрани существующий `data/admarket.db`. Установи зависимости из `requirements.txt`, добавь `SANDBOX_WEBHOOK_SECRET` (32+ случайных символа для локального тестового webhook), затем запусти приложение. При старте выполняются аддитивные миграции: новые таблицы и `escrow_status` добавляются без удаления старых записей. Существующие v0.9 успешные тестовые платежи сохраняют прежние ledger-проводки; новые платежи используют новый статус эскроу.


## Обновление с v1.1 до v1.2

Останови сервер и сделай резервную копию всего каталога `data` (особенно `admarket.db` и `dispute_evidence`). Замени код приложения файлами v1.2, сохранив существующую базу и каталог `data`. При старте выполняются аддитивные миграции: создаются таблицы споров/доказательств и защитный индекс выпуска; старые записи не удаляются. После запуска проверь `/health` (версия `1.3.0`), войди под рекламодателем/поставщиком и проверь кабинет; администратору доступны `/admin`, `/admin/reconciliation` и раздел споров.



## v1.5 — индекс качества и антифрод

Индекс поставщика — внутренний объяснимый сигнал: отзывы 40%, выполнение SLA 30%, активные жалобы 20%, статус внутренней проверки 10%. При отсутствии истории используются нейтральные стартовые значения. Отклонённые/закрытые жалобы не считаются доказанными нарушениями. Антифрод-сканер ищет точные совпадения параметров объявлений у разных поставщиков и крайние значения цены/аудитории; это только очередь ручной проверки. Автоматического бана, отклонения, скрытого штрафа или гарантии обнаружения мошенничества нет.

Для обновления останови сервер и сделай резервную копию всего каталога `data`. Замени код приложения файлами v1.5, сохрани существующие `data/admarket.db` и `data/dispute_evidence`. При старте создаются новые таблицы `supplier_quality_snapshots` и `fraud_review_signals`; существующие записи не удаляются. После запуска проверь `/health`, `/admin/quality.json` под администратором и разделы «Индекс качества» и «Антифрод» в `/admin`.

## Обновление с v1.2 до v1.4

Останови сервер и сделай резервную копию каталога `data`. Замени код файлами v1.4, сохрани `data/admarket.db` и `data/dispute_evidence`. При запуске создаётся таблица `placement_delivery_receipts` и индекс; старые данные не удаляются. Для новых отметок публикации поставщик указывает HTTPS-ссылку, рекламодатель принимает её кнопкой подтверждения выполнения.


## v1.6 — проверка рекламных площадок

Поставщик может добавить публичный HTTPS-профиль площадки и периодически отправлять снимки статистики (подписчики, средние просмотры, охват, ER). Снимки неизменяемы. Система сравнивает два последних снимка и создаёт объяснимые сигналы при резких изменениях и нетипичных соотношениях. Администратор вручную проверяет профиль и сигналы.

Ограничение: статистика вводится самим поставщиком; API социальных сетей и независимый сбор метрик не подключены. Ссылка — лишь ссылка, а не доказательство владения. Сигналы не доказывают накрутку и не блокируют аккаунты.


## v1.8 — полноценная версия для ПК и адаптивный интерфейс

Интерфейс переработан для широких экранов: увеличена рабочая ширина, улучшена сетка каталога и кабинетов, добавлен desktop-превью медиаплана на главной, улучшены таблицы и формы. На планшетах и телефонах сохраняется адаптивная компоновка. Функциональность, серверные права доступа и существующие миграции сохранены.


## v1.9 — Dark Premium workspace

Единая дизайн-система ADMARKET: графитовый фон, лаймовые акценты, обновлённая типографика, навигация, карточки, формы и состояния интерфейса. Главная страница подчёркивает AI-медиабайер, конструктор медиапланов и каталог. Каталог получил фильтры по каналу, географии, цене и размеру аудитории, сортировку и сравнение до трёх площадок. Кабинет получил быстрые переходы по ролям, аналитика — KPI и отдельный блок тестового денежного потока, журнал операций и админ-панель — более читаемую структуру.

Обновление не меняет схему SQLite, существующие данные, маршруты бизнес-процессов, миграции или правила авторизации. Все реальные платежи по-прежнему отключены: доступен только sandbox. Для обновления останови сервер, создай резервную копию каталога `data`, затем замени код приложения, сохранив `data/admarket.db` и `data/dispute_evidence`.

## v2.0 — персональные рабочие пространства

- Рекламодатель получает сводку по своим заявкам, ожидающим откликам, активным и завершённым размещениям.
- Поставщик видит свои предложения, ожидающую модерацию, активные/завершённые размещения и доступные рекламные задачи.
- Добавлен блок «Следующие действия» с прямыми переходами к нужным разделам: отклики, размещения, площадки, уведомления и создание заявки.
- Администратор получает агрегированную сводку и защищённый переход в панель управления только при прохождении прежней серверной allowlist-проверки.
- Показатели берутся из записей текущего пользователя; чужие заявки/размещения не попадают в ролевую сводку.
- Новых таблиц и миграций нет: используется существующая схема SQLite. Все старые функции и sandbox-ограничения сохранены.

### Обновление до v2.0

Останови сервер, сделай резервную копию всего каталога `data`, затем замени файлы приложения из архива, сохранив `data/admarket.db` и `data/dispute_evidence`. Новых миграций нет. После запуска проверь `/health` — версия должна быть `2.0.0`.



## v2.1 — управление рекламными кампаниями

- Добавлена отдельная сущность кампании рекламодателя с бюджетом, целью, географией, каналами, аудиторией и периодом.
- Кампанию можно связать только со своей заявкой.
- Жизненный цикл: черновик, активна, приостановлена, завершена, архив. Архивную кампанию нельзя возобновить; завершённую нельзя повторно активировать.
- Все изменения статуса фиксируются в `campaign_events`.
- Доступ к кампаниям ограничен владельцем-рекламодателем; CSRF-проверка обязательна.
- Добавлена безопасная аддитивная миграция SQLite; существующие таблицы и данные не удаляются.

### Обновление до v2.1

Останови сервер и сделай резервную копию `data`. Распакуй новую версию поверх приложения, сохранив каталог `data`. При первом запуске `init_db()` создаст таблицы `campaigns` и `campaign_events`. Проверь `/health`: для этой версии должна быть `2.1.0`. Платежи по-прежнему только sandbox; ключи и база данных в ZIP не включены.


### v2.2 — AI Media Planning
Advertisers can open `/media-planner`, create a budgeted plan from the current catalog, save an immutable JSON snapshot, and attach it to one of their own non-completed campaigns. AI strategy commentary requires a server-side OpenAI key; deterministic planning and fallback explanations work without it. Plans use demo inventory where indicated and do not reserve placements, initiate payments, or guarantee reach.


### Обновление до v2.2
Останови сервер и сохрани резервную копию каталога `data` (включая `admarket.db` и `dispute_evidence`). Распакуй v2.3 поверх приложения, не удаляя `data`. При первом запуске `init_db()` аддитивно создаст `media_plans`, `campaign_metric_snapshots` и индексы; существующие кампании, сделки, медиапланы и финансовые записи сохраняются. Проверь `/health`: версия должна быть `2.3.0`. AI-комментарий требует серверного `OPENAI_API_KEY`; сам ключ в ZIP не включён. Все платежи остаются в sandbox.


## v2.3.0 — аналитика кампаний

Добавлена дневная таблица `campaign_metric_snapshots` с уникальностью по кампании и дате. Рекламодатель может вручную вносить расход, показы, клики, конверсии и необязательную выручку, просматривать агрегированные KPI и экспортировать историю в CSV. Доступ проверяется по владельцу кампании на сервере. CTR, CPC, CPM, conversion rate, CPA и ROI рассчитываются только из внесённых данных; при отсутствии знаменателя или полной выручки показывается пустое значение. Подключения к рекламным кабинетам и автоматической проверки источника пока нет.


## v2.4.0 — CSV-импорт рекламной статистики

- В аналитике кампании появился импорт дневных показателей из UTF-8 CSV. Обязательные колонки: `date,spend,impressions,clicks,conversions`; необязательные: `revenue,note`.
- Ограничения: 2 МБ, до 366 строк за импорт; проверяются даты, целые неотрицательные значения, диапазоны и `clicks <= impressions`. Некорректный файл отклоняется целиком.
- Данные сохраняются с источником `import`; повторный импорт той же даты обновляет дневную запись. Доступ ограничен владельцем кампании.
- Это импорт отчёта, а не прямое подключение к рекламному кабинету. OAuth/API-коннекторы не активированы и не запрашивают токены. Для реальной синхронизации нужны официальные приложения, ключи, разрешения и проверка условий каждой рекламной платформы.

Пример формата: `examples/campaign_metrics_template.csv`. Замените демонстрационную строку фактическими данными перед импортом.


## v2.5 — рекламные интеграции

Новый раздел `/integrations` доступен рекламодателям. Yandex Direct OAuth включается только при настройке серверных переменных `YANDEX_DIRECT_CLIENT_ID`, `YANDEX_DIRECT_CLIENT_SECRET`, `YANDEX_DIRECT_REDIRECT_URI` и `INTEGRATION_ENCRYPTION_KEY`. Сгенерировать ключ можно командой `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`. Используй HTTPS и точное совпадение callback URI. Потеря ключа шифрования делает сохранённые токены нечитаемыми.

В v2.5 OAuth ещё не запускает синхронизацию статистики. Для Яндекс Директа отдельно требуется получение доступа к API; VK Ads пока отображается как провайдер в разработке.

Официальная документация: [доступ к API Директа](https://yandex.ru/support/direct/en/alternative-interfaces/api), [условия авторизации и допуска](https://yandex.com/dev/direct/doc/en/concepts/access), [пример OAuth authorization-code](https://yandex.ru/dev/direct/doc/en/php5-file_get_contents-token). После локального удаления токена разрешение приложения на стороне Яндекс ID может сохраняться — отзови его отдельно в настройках аккаунта, если хочешь полностью прекратить доступ.


## v2.6 — Partner Promotion

Поставщики указывают HTTPS-ссылку на публикацию с рекламой ADMARKET при регистрации либо позже в кабинете. Администратор вручную проверяет фактическое размещение. До подтверждения сервер запрещает создавать рекламные предложения. После подтверждения поставщик получает персональную ссылку `/r/{code}`; система считает переходы по сессиям браузера и регистрации новых рекламодателей. IP-адреса и user-agent не сохраняются. Атрибуция не означает продажу, начисление комиссии или выплату. Реальные реферальные выплаты не реализованы.


## v2.7 — Partner Analytics
Поставщик видит переходы и регистрации за 7/30 дней, конверсию за всё время и ежедневную динамику за 14 дней. Из кабинета можно скачать CSV за 7, 30, 90 или 365 дней. Экспорт содержит только агрегаты по дням, не раскрывает email, ID, IP или данные браузера и доступен только вошедшему поставщику-владельцу. Администратор видит конверсию в очереди Partner Promotion.

Обновление с v2.6 не требует миграции. Останови сервер, сделай резервную копию `data/` и `.env`, замени код и перезапусти приложение. Не удаляй существующую базу. Платежи и выплаты партнёрам не включены.


### v2.8 Partner Rewards
Partner rewards are disabled by default (0 ₽). Admin can configure a fixed sandbox bonus; accrual requires an attributed advertiser, approved partner promotion, completed placement, successful sandbox payment, and manual sandbox escrow release. Admin approval and `paid_sandbox` are internal test states only. No real payout is performed. See `RELEASE_NOTES_v2.8.md`.
