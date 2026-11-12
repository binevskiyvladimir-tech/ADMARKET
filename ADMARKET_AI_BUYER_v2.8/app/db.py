import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
DB_PATH = DATA / "admarket.db"

SEED = [
    ("VK Сообщество «Городские события»", "VK", "Город / афиша", "Омск", 42000, 6500, "Пост, клип", "Демо-площадка. Контакт не подтверждён."),
    ("VK Сообщество «Технологии просто»", "VK", "Технологии", "Россия", 118000, 18000, "Пост, нативный обзор", "Демо-площадка. Контакт не подтверждён."),
    ("Медиа «Бизнес Среда»", "Сайт / медиа", "Бизнес", "Россия", 76000, 24000, "Статья, баннер", "Демо-площадка. Контакт не подтверждён."),
    ("Рассылка «Маркетинг по делу»", "Email", "Маркетинг", "Россия", 12500, 9000, "Выпуск рассылки", "Демо-площадка. Контакт не подтверждён."),
    ("Подкаст «Делают бизнес»", "Подкаст", "Предпринимательство", "Россия", 28000, 16000, "Интеграция 45–60 сек.", "Демо-площадка. Контакт не подтверждён."),
    ("VK Сообщество «Дом и ремонт»", "VK", "Недвижимость / ремонт", "Сибирь", 53000, 8500, "Пост, клип", "Демо-площадка. Контакт не подтверждён."),
    ("Портал «Выбор покупателя»", "Сайт / медиа", "Потребительские товары", "Россия", 94000, 21000, "Обзор, баннер", "Демо-площадка. Контакт не подтверждён."),
    ("MAX Канал «Новости района»", "MAX", "Город / новости", "Омск", 17000, 3500, "Публикация", "Демо-площадка. Доступность рекламы и условия требуют проверки."),
]

def connect():
    DATA.mkdir(exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con

def _columns(con, table):
    return {row[1] for row in con.execute(f"PRAGMA table_info({table})")}

def init_db():
    with connect() as con:
        con.execute("""CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT NOT NULL UNIQUE COLLATE NOCASE,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL CHECK(role IN ('advertiser','supplier','admin')),
            name TEXT NOT NULL DEFAULT '',
            company TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            is_active INTEGER NOT NULL DEFAULT 1,
            email_verified INTEGER NOT NULL DEFAULT 1 CHECK(email_verified IN (0,1))
        )""")
        con.execute("""CREATE TABLE IF NOT EXISTS inventory (
            id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, channel TEXT NOT NULL,
            topic TEXT NOT NULL, geo TEXT NOT NULL, audience INTEGER NOT NULL,
            price INTEGER NOT NULL, formats TEXT NOT NULL, notes TEXT NOT NULL,
            demo INTEGER NOT NULL DEFAULT 1
        )""")
        con.execute("""CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT, company TEXT NOT NULL, contact TEXT NOT NULL,
            goal TEXT NOT NULL, budget INTEGER NOT NULL, geo TEXT NOT NULL, selected TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, status TEXT NOT NULL DEFAULT 'Новая',
            user_id INTEGER REFERENCES users(id) ON DELETE SET NULL
        )""")
        # Safe additive migration for databases created by v0.2.
        if "user_id" not in _columns(con, "orders"):
            con.execute("ALTER TABLE orders ADD COLUMN user_id INTEGER REFERENCES users(id) ON DELETE SET NULL")
        con.execute("""CREATE TABLE IF NOT EXISTS supplier_offers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            supplier_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            name TEXT NOT NULL, channel TEXT NOT NULL, topic TEXT NOT NULL,
            geo TEXT NOT NULL, audience INTEGER NOT NULL DEFAULT 0,
            price INTEGER NOT NULL, formats TEXT NOT NULL, details TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','approved','rejected')),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )""")
        con.execute("""CREATE TABLE IF NOT EXISTS advertiser_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            advertiser_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            title TEXT NOT NULL, goal TEXT NOT NULL, geo TEXT NOT NULL,
            budget INTEGER NOT NULL, audience TEXT NOT NULL DEFAULT '',
            channels TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'Открыта',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )""")
        # Supplier-to-advertiser proposals: a response belongs to one request and one supplier.
        con.execute("""CREATE TABLE IF NOT EXISTS request_responses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            request_id INTEGER NOT NULL REFERENCES advertiser_requests(id) ON DELETE CASCADE,
            supplier_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            supplier_offer_id INTEGER REFERENCES supplier_offers(id) ON DELETE SET NULL,
            proposed_price INTEGER NOT NULL CHECK(proposed_price >= 1),
            message TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','accepted','rejected')),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(request_id, supplier_id)
        )""")
        # v0.5: operational placement lifecycle and private conversation per accepted response.
        con.execute("""CREATE TABLE IF NOT EXISTS placements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            response_id INTEGER NOT NULL UNIQUE REFERENCES request_responses(id) ON DELETE CASCADE,
            status TEXT NOT NULL DEFAULT 'awaiting_supplier' CHECK(status IN
                ('awaiting_supplier','confirmed','published','completed','cancelled')),
            supplier_note TEXT NOT NULL DEFAULT '',
            publication_url TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )""")
        con.execute("""CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            response_id INTEGER NOT NULL REFERENCES request_responses(id) ON DELETE CASCADE,
            sender_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            body TEXT NOT NULL CHECK(length(body) BETWEEN 1 AND 3000),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )""")
        con.execute("CREATE INDEX IF NOT EXISTS idx_messages_response ON messages(response_id, id)")
        # v0.6: immutable snapshots of proposed commercial terms, inbox notifications and audit trail.
        con.execute("""CREATE TABLE IF NOT EXISTS placement_terms (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            placement_id INTEGER NOT NULL REFERENCES placements(id) ON DELETE CASCADE,
            version INTEGER NOT NULL,
            schedule TEXT NOT NULL DEFAULT '',
            deliverables TEXT NOT NULL,
            price INTEGER NOT NULL CHECK(price >= 1),
            payment_note TEXT NOT NULL DEFAULT '',
            cancellation_note TEXT NOT NULL DEFAULT '',
            extra_terms TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'proposed' CHECK(status IN ('proposed','accepted','rejected','superseded')),
            proposed_by INTEGER NOT NULL REFERENCES users(id),
            supplier_accepted_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            advertiser_accepted_at TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(placement_id, version)
        )""")
        con.execute("CREATE INDEX IF NOT EXISTS idx_terms_placement ON placement_terms(placement_id, version DESC)")
        con.execute("""CREATE TABLE IF NOT EXISTS notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            kind TEXT NOT NULL,
            title TEXT NOT NULL,
            body TEXT NOT NULL DEFAULT '',
            target_url TEXT NOT NULL DEFAULT '/dashboard',
            is_read INTEGER NOT NULL DEFAULT 0 CHECK(is_read IN (0,1)),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )""")
        con.execute("CREATE INDEX IF NOT EXISTS idx_notifications_user ON notifications(user_id, is_read, id DESC)")
        con.execute("""CREATE TABLE IF NOT EXISTS activity_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            actor_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
            placement_id INTEGER REFERENCES placements(id) ON DELETE SET NULL,
            event_type TEXT NOT NULL,
            summary TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )""")
        con.execute("CREATE INDEX IF NOT EXISTS idx_activity_placement ON activity_log(placement_id, id DESC)")
        # v0.7: immutable financial estimates; these are calculations only, never balances or payments.
        con.execute("""CREATE TABLE IF NOT EXISTS financial_estimates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            placement_id INTEGER NOT NULL REFERENCES placements(id) ON DELETE CASCADE,
            terms_id INTEGER NOT NULL UNIQUE REFERENCES placement_terms(id) ON DELETE CASCADE,
            advertiser_id INTEGER NOT NULL REFERENCES users(id),
            supplier_id INTEGER NOT NULL REFERENCES users(id),
            base_amount INTEGER NOT NULL CHECK(base_amount >= 1),
            commission_rate REAL NOT NULL CHECK(commission_rate >= 0 AND commission_rate <= 100),
            commission_amount INTEGER NOT NULL CHECK(commission_amount >= 0),
            supplier_estimated_net INTEGER NOT NULL CHECK(supplier_estimated_net >= 0),
            advertiser_estimated_total INTEGER NOT NULL CHECK(advertiser_estimated_total >= 0),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )""")
        con.execute("CREATE INDEX IF NOT EXISTS idx_finance_advertiser ON financial_estimates(advertiser_id, id DESC)")
        # v0.8: email verification/reset tokens, throttling records and editable platform settings.
        if "email_verified" not in _columns(con, "users"):
            con.execute("ALTER TABLE users ADD COLUMN email_verified INTEGER NOT NULL DEFAULT 1")
        con.execute("""CREATE TABLE IF NOT EXISTS account_tokens (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            purpose TEXT NOT NULL CHECK(purpose IN ('verify_email','reset_password')),
            token_hash TEXT NOT NULL UNIQUE,
            expires_at TEXT NOT NULL,
            used_at TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )""")
        con.execute("CREATE INDEX IF NOT EXISTS idx_account_tokens_user ON account_tokens(user_id,purpose,created_at DESC)")
        con.execute("""CREATE TABLE IF NOT EXISTS security_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT NOT NULL,
            event_type TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )""")
        con.execute("CREATE INDEX IF NOT EXISTS idx_security_events_email ON security_events(email,event_type,created_at DESC)")
        con.execute("""CREATE TABLE IF NOT EXISTS app_settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )""")
        con.execute("INSERT OR IGNORE INTO app_settings(key,value) VALUES('commission_rate','12')")
        con.execute("CREATE INDEX IF NOT EXISTS idx_finance_supplier ON financial_estimates(supplier_id, id DESC)")
        # Backfill accepted v0.6 terms so existing agreements appear in the estimate history.
        con.execute("""INSERT OR IGNORE INTO financial_estimates
            (placement_id,terms_id,advertiser_id,supplier_id,base_amount,commission_rate,commission_amount,
             supplier_estimated_net,advertiser_estimated_total)
            SELECT p.id,pt.id,ar.advertiser_id,rr.supplier_id,pt.price,12.0,
                   CAST(ROUND(pt.price * 0.12) AS INTEGER),
                   MAX(0,pt.price - CAST(ROUND(pt.price * 0.12) AS INTEGER)),pt.price
            FROM placement_terms pt JOIN placements p ON p.id=pt.placement_id
            JOIN request_responses rr ON rr.id=p.response_id
            JOIN advertiser_requests ar ON ar.id=rr.request_id
            WHERE pt.status='accepted'""")
        # Backfill placement records for accepted selections created in earlier versions.
        con.execute("""INSERT OR IGNORE INTO placements(response_id)
            SELECT id FROM request_responses WHERE status='accepted'""")
        # Make v0.3's default "Новая" advertiser briefs visible to suppliers in v0.4.
        con.execute("UPDATE advertiser_requests SET status='Открыта' WHERE status='Новая'")
        con.execute("CREATE INDEX IF NOT EXISTS idx_requests_status ON advertiser_requests(status, created_at)")
        con.execute("CREATE INDEX IF NOT EXISTS idx_responses_request ON request_responses(request_id, status)")
        con.execute("CREATE INDEX IF NOT EXISTS idx_offers_status ON supplier_offers(status, created_at)")
        # v0.9: sandbox payment intents, balanced ledger, refunds, supplier verification, reviews and risk signals.
        if "supplier_verification" not in _columns(con, "users"):
            con.execute("ALTER TABLE users ADD COLUMN supplier_verification TEXT NOT NULL DEFAULT 'unverified'")
        if "risk_level" not in _columns(con, "users"):
            con.execute("ALTER TABLE users ADD COLUMN risk_level TEXT NOT NULL DEFAULT 'normal'")
        con.execute("""CREATE TABLE IF NOT EXISTS payment_intents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            placement_id INTEGER NOT NULL REFERENCES placements(id),
            terms_id INTEGER NOT NULL UNIQUE REFERENCES placement_terms(id),
            advertiser_id INTEGER NOT NULL REFERENCES users(id),
            supplier_id INTEGER NOT NULL REFERENCES users(id),
            amount INTEGER NOT NULL CHECK(amount > 0),
            commission_rate REAL NOT NULL CHECK(commission_rate BETWEEN 0 AND 30),
            commission_amount INTEGER NOT NULL CHECK(commission_amount >= 0),
            supplier_amount INTEGER NOT NULL CHECK(supplier_amount >= 0),
            provider TEXT NOT NULL DEFAULT 'sandbox',
            mode TEXT NOT NULL DEFAULT 'test' CHECK(mode='test'),
            status TEXT NOT NULL DEFAULT 'created' CHECK(status IN ('created','succeeded','failed','refunded')),
            provider_reference TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            paid_at TEXT
        )""")
        con.execute("CREATE INDEX IF NOT EXISTS idx_payment_intents_advertiser ON payment_intents(advertiser_id,id DESC)")
        con.execute("""CREATE TABLE IF NOT EXISTS ledger_transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            payment_intent_id INTEGER REFERENCES payment_intents(id),
            refund_of INTEGER REFERENCES ledger_transactions(id),
            kind TEXT NOT NULL CHECK(kind IN ('sandbox_payment','refund')),
            description TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )""")
        con.execute("""CREATE TABLE IF NOT EXISTS ledger_entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            transaction_id INTEGER NOT NULL REFERENCES ledger_transactions(id) ON DELETE CASCADE,
            account_type TEXT NOT NULL CHECK(account_type IN ('user','platform','external')),
            user_id INTEGER REFERENCES users(id),
            amount INTEGER NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            CHECK((account_type='user' AND user_id IS NOT NULL) OR (account_type!='user' AND user_id IS NULL))
        )""")
        con.execute("CREATE INDEX IF NOT EXISTS idx_ledger_user ON ledger_entries(user_id,id DESC)")
        con.execute("""CREATE TABLE IF NOT EXISTS refunds (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            payment_intent_id INTEGER NOT NULL UNIQUE REFERENCES payment_intents(id),
            ledger_transaction_id INTEGER REFERENCES ledger_transactions(id),
            amount INTEGER NOT NULL CHECK(amount > 0),
            reason TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'processed' CHECK(status IN ('processed','rejected')),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )""")
        con.execute("""CREATE TABLE IF NOT EXISTS supplier_verification_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            supplier_id INTEGER NOT NULL REFERENCES users(id),
            admin_id INTEGER NOT NULL REFERENCES users(id),
            status TEXT NOT NULL CHECK(status IN ('unverified','pending','verified','rejected')),
            note TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )""")
        con.execute("""CREATE TABLE IF NOT EXISTS reviews (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            placement_id INTEGER NOT NULL REFERENCES placements(id),
            reviewer_id INTEGER NOT NULL REFERENCES users(id),
            reviewee_id INTEGER NOT NULL REFERENCES users(id),
            rating INTEGER NOT NULL CHECK(rating BETWEEN 1 AND 5),
            body TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(placement_id, reviewer_id)
        )""")
        con.execute("""CREATE TABLE IF NOT EXISTS risk_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER REFERENCES users(id),
            placement_id INTEGER REFERENCES placements(id),
            event_type TEXT NOT NULL,
            severity TEXT NOT NULL DEFAULT 'low' CHECK(severity IN ('low','medium','high')),
            details TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open','reviewed','dismissed')),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )""")
        con.execute("CREATE INDEX IF NOT EXISTS idx_risk_events_status ON risk_events(status,severity,id DESC)")
        # v1.2: placement disputes, evidence metadata and payout idempotency guard.
        con.execute("""CREATE TABLE IF NOT EXISTS disputes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            placement_id INTEGER NOT NULL REFERENCES placements(id),
            opened_by INTEGER NOT NULL REFERENCES users(id),
            reason TEXT NOT NULL CHECK(length(reason) BETWEEN 10 AND 3000),
            status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open','in_review','resolved_for_advertiser','resolved_for_supplier','closed')),
            resolution TEXT NOT NULL DEFAULT '',
            reviewer_id INTEGER REFERENCES users(id),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )""")
        con.execute("CREATE INDEX IF NOT EXISTS idx_disputes_placement ON disputes(placement_id,id DESC)")
        con.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_disputes_one_active_per_placement ON disputes(placement_id) WHERE status IN ('open','in_review')")
        con.execute("""CREATE TABLE IF NOT EXISTS dispute_evidence (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            dispute_id INTEGER NOT NULL REFERENCES disputes(id) ON DELETE CASCADE,
            uploaded_by INTEGER NOT NULL REFERENCES users(id),
            evidence_type TEXT NOT NULL CHECK(evidence_type IN ('file','link')),
            original_name TEXT NOT NULL DEFAULT '',
            stored_name TEXT NOT NULL DEFAULT '',
            mime_type TEXT NOT NULL DEFAULT '',
            size_bytes INTEGER NOT NULL DEFAULT 0,
            sha256 TEXT NOT NULL DEFAULT '',
            external_url TEXT NOT NULL DEFAULT '',
            note TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )""")
        con.execute("CREATE INDEX IF NOT EXISTS idx_dispute_evidence_dispute ON dispute_evidence(dispute_id,id)")
        con.execute("CREATE INDEX IF NOT EXISTS idx_dispute_evidence_hash ON dispute_evidence(sha256)")
        # v1.3: immutable publication proof and advertiser acceptance snapshot.
        con.execute("""CREATE TABLE IF NOT EXISTS placement_delivery_receipts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            placement_id INTEGER NOT NULL UNIQUE REFERENCES placements(id) ON DELETE CASCADE,
            supplier_id INTEGER NOT NULL REFERENCES users(id),
            publication_url TEXT NOT NULL CHECK(length(publication_url) BETWEEN 8 AND 1000),
            submitted_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            accepted_by INTEGER REFERENCES users(id),
            accepted_at TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'submitted' CHECK(status IN ('submitted','accepted'))
        )""")
        con.execute("CREATE INDEX IF NOT EXISTS idx_delivery_receipts_status ON placement_delivery_receipts(status,submitted_at DESC)")
        con.execute("""CREATE TRIGGER IF NOT EXISTS trg_delivery_receipt_proof_immutable
            BEFORE UPDATE OF placement_id,supplier_id,publication_url,submitted_at ON placement_delivery_receipts
            BEGIN SELECT RAISE(ABORT,'publication proof fields are immutable'); END""")
        # v1.4: supplier service-level tracking and structured complaints.
        con.execute("""CREATE TABLE IF NOT EXISTS supplier_sla_cases (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            placement_id INTEGER NOT NULL UNIQUE REFERENCES placements(id) ON DELETE CASCADE,
            supplier_id INTEGER NOT NULL REFERENCES users(id),
            due_at TEXT NOT NULL,
            fulfilled_at TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','met','breached','cancelled')),
            breached_at TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )""")
        con.execute("CREATE INDEX IF NOT EXISTS idx_supplier_sla_status_due ON supplier_sla_cases(status,due_at)")
        con.execute("""CREATE TABLE IF NOT EXISTS supplier_complaints (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            placement_id INTEGER NOT NULL REFERENCES placements(id),
            opened_by INTEGER NOT NULL REFERENCES users(id),
            supplier_id INTEGER NOT NULL REFERENCES users(id),
            category TEXT NOT NULL CHECK(category IN ('late_delivery','quality','communication','misrepresentation','other')),
            description TEXT NOT NULL CHECK(length(description) BETWEEN 10 AND 2500),
            status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open','in_review','resolved','dismissed')),
            resolution TEXT NOT NULL DEFAULT '',
            reviewer_id INTEGER REFERENCES users(id),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )""")
        con.execute("CREATE INDEX IF NOT EXISTS idx_supplier_complaints_status ON supplier_complaints(status,id DESC)")
        con.execute("""CREATE TABLE IF NOT EXISTS supplier_complaint_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            complaint_id INTEGER NOT NULL REFERENCES supplier_complaints(id) ON DELETE CASCADE,
            actor_id INTEGER REFERENCES users(id),
            event_type TEXT NOT NULL,
            details TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )""")
        con.execute("CREATE INDEX IF NOT EXISTS idx_complaint_events ON supplier_complaint_events(complaint_id,id)")
        # v1.5: transparent supplier quality snapshots and deduplicated anti-fraud review signals.
        con.execute("""CREATE TABLE IF NOT EXISTS supplier_quality_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            supplier_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            score INTEGER NOT NULL CHECK(score BETWEEN 0 AND 100),
            rating_score INTEGER NOT NULL CHECK(rating_score BETWEEN 0 AND 100),
            sla_score INTEGER NOT NULL CHECK(sla_score BETWEEN 0 AND 100),
            complaint_score INTEGER NOT NULL CHECK(complaint_score BETWEEN 0 AND 100),
            verification_score INTEGER NOT NULL CHECK(verification_score BETWEEN 0 AND 100),
            review_count INTEGER NOT NULL DEFAULT 0,
            sla_count INTEGER NOT NULL DEFAULT 0,
            complaint_count INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )""")
        con.execute("CREATE INDEX IF NOT EXISTS idx_supplier_quality_latest ON supplier_quality_snapshots(supplier_id,id DESC)")
        con.execute("""CREATE TABLE IF NOT EXISTS fraud_review_signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            signal_key TEXT NOT NULL UNIQUE,
            signal_type TEXT NOT NULL,
            severity TEXT NOT NULL CHECK(severity IN ('low','medium','high')),
            supplier_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
            offer_id INTEGER REFERENCES supplier_offers(id) ON DELETE SET NULL,
            details TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open','reviewed','dismissed')),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            reviewed_at TEXT NOT NULL DEFAULT ''
        )""")
        con.execute("CREATE INDEX IF NOT EXISTS idx_fraud_signals_status ON fraud_review_signals(status,severity,id DESC)")
        # v1.0: sandbox escrow lifecycle, signed webhook idempotency and moderation audit.
        if "escrow_status" not in _columns(con, "payment_intents"):
            con.execute("ALTER TABLE payment_intents ADD COLUMN escrow_status TEXT NOT NULL DEFAULT 'not_applicable'")
        con.execute("""CREATE TABLE IF NOT EXISTS webhook_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            provider TEXT NOT NULL DEFAULT 'sandbox',
            event_id TEXT NOT NULL UNIQUE,
            event_type TEXT NOT NULL,
            payload_hash TEXT NOT NULL,
            processed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            result TEXT NOT NULL DEFAULT 'processed'
        )""")
        con.execute("CREATE INDEX IF NOT EXISTS idx_webhook_events_processed ON webhook_events(processed_at DESC)")
        con.execute("""CREATE TABLE IF NOT EXISTS escrow_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            payment_intent_id INTEGER NOT NULL REFERENCES payment_intents(id),
            actor_id INTEGER REFERENCES users(id),
            event_type TEXT NOT NULL CHECK(event_type IN ('funds_held','funds_released','funds_refunded')),
            amount INTEGER NOT NULL CHECK(amount > 0),
            note TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )""")
        con.execute("CREATE INDEX IF NOT EXISTS idx_escrow_payment ON escrow_events(payment_intent_id,id DESC)")
        con.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_escrow_release_once ON escrow_events(payment_intent_id) WHERE event_type='funds_released'")
        con.execute("""CREATE TABLE IF NOT EXISTS moderation_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entity_type TEXT NOT NULL CHECK(entity_type IN ('supplier_offer','advertiser_request')),
            entity_id INTEGER NOT NULL,
            score INTEGER NOT NULL CHECK(score BETWEEN 0 AND 100),
            decision TEXT NOT NULL CHECK(decision IN ('clear','review')),
            reasons TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(entity_type,entity_id)
        )""")
        if "review_status" not in _columns(con, "moderation_records"):
            con.execute("ALTER TABLE moderation_records ADD COLUMN review_status TEXT NOT NULL DEFAULT 'pending'")
        con.execute("CREATE INDEX IF NOT EXISTS idx_moderation_queue ON moderation_records(decision,review_status,created_at DESC)")
        # v1.6: platform account registry, immutable metric history and explainable audience-trust signals.
        con.execute("""CREATE TABLE IF NOT EXISTS platform_accounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            supplier_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            platform TEXT NOT NULL CHECK(length(platform) BETWEEN 2 AND 40),
            handle TEXT NOT NULL CHECK(length(handle) BETWEEN 2 AND 120),
            profile_url TEXT NOT NULL CHECK(length(profile_url) BETWEEN 8 AND 1000),
            verification_status TEXT NOT NULL DEFAULT 'pending' CHECK(verification_status IN ('pending','verified','rejected')),
            verification_note TEXT NOT NULL DEFAULT '',
            verified_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
            verified_at TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(supplier_id,platform,handle)
        )""")
        con.execute("CREATE INDEX IF NOT EXISTS idx_platform_accounts_supplier ON platform_accounts(supplier_id,id DESC)")
        con.execute("""CREATE TABLE IF NOT EXISTS platform_metric_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id INTEGER NOT NULL REFERENCES platform_accounts(id) ON DELETE CASCADE,
            followers INTEGER NOT NULL CHECK(followers >= 0 AND followers <= 2000000000),
            avg_views INTEGER NOT NULL CHECK(avg_views >= 0 AND avg_views <= 2000000000),
            avg_reach INTEGER NOT NULL CHECK(avg_reach >= 0 AND avg_reach <= 2000000000),
            engagement_rate REAL NOT NULL CHECK(engagement_rate >= 0 AND engagement_rate <= 100),
            source_label TEXT NOT NULL DEFAULT 'self_reported',
            evidence_url TEXT NOT NULL DEFAULT '',
            sampled_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )""")
        con.execute("CREATE INDEX IF NOT EXISTS idx_platform_metrics_history ON platform_metric_snapshots(account_id,sampled_at DESC,id DESC)")
        con.execute("""CREATE TRIGGER IF NOT EXISTS trg_platform_metrics_immutable
            BEFORE UPDATE ON platform_metric_snapshots
            BEGIN SELECT RAISE(ABORT,'platform metric snapshots are immutable'); END""")
        con.execute("""CREATE TABLE IF NOT EXISTS platform_trust_assessments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id INTEGER NOT NULL REFERENCES platform_accounts(id) ON DELETE CASCADE,
            trust_score INTEGER NOT NULL CHECK(trust_score BETWEEN 0 AND 100),
            confidence TEXT NOT NULL CHECK(confidence IN ('insufficient','low','medium','high')),
            flags TEXT NOT NULL DEFAULT '',
            previous_followers INTEGER NOT NULL DEFAULT 0,
            current_followers INTEGER NOT NULL DEFAULT 0,
            growth_pct REAL NOT NULL DEFAULT 0,
            view_to_follower_ratio REAL NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )""")
        con.execute("CREATE INDEX IF NOT EXISTS idx_platform_assessments_account ON platform_trust_assessments(account_id,id DESC)")
        con.execute("""CREATE TABLE IF NOT EXISTS platform_trust_signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            signal_key TEXT NOT NULL UNIQUE,
            account_id INTEGER NOT NULL REFERENCES platform_accounts(id) ON DELETE CASCADE,
            snapshot_id INTEGER NOT NULL REFERENCES platform_metric_snapshots(id) ON DELETE CASCADE,
            signal_type TEXT NOT NULL,
            severity TEXT NOT NULL CHECK(severity IN ('low','medium','high')),
            details TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open','reviewed','dismissed')),
            reviewed_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
            reviewed_at TEXT NOT NULL DEFAULT '',
            review_note TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )""")
        con.execute("CREATE INDEX IF NOT EXISTS idx_platform_trust_signals_status ON platform_trust_signals(status,severity,id DESC)")
        # v2.1: advertiser campaign workspace with immutable status history.
        con.execute("""CREATE TABLE IF NOT EXISTS campaigns (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            advertiser_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            request_id INTEGER REFERENCES advertiser_requests(id) ON DELETE SET NULL,
            title TEXT NOT NULL CHECK(length(title) BETWEEN 2 AND 140),
            objective TEXT NOT NULL DEFAULT '',
            budget INTEGER NOT NULL CHECK(budget BETWEEN 1000 AND 100000000),
            geo TEXT NOT NULL DEFAULT '',
            channels TEXT NOT NULL DEFAULT '',
            audience TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'draft' CHECK(status IN ('draft','active','paused','completed','archived')),
            start_date TEXT NOT NULL DEFAULT '',
            end_date TEXT NOT NULL DEFAULT '',
            media_plan_json TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )""")
        con.execute("CREATE INDEX IF NOT EXISTS idx_campaigns_advertiser_status ON campaigns(advertiser_id,status,id DESC)")
        con.execute("""CREATE TABLE IF NOT EXISTS campaign_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            campaign_id INTEGER NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
            actor_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
            event_type TEXT NOT NULL,
            details TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )""")
        con.execute("CREATE INDEX IF NOT EXISTS idx_campaign_events_campaign ON campaign_events(campaign_id,id DESC)")
        # v2.2: saved media plans are advertiser-owned snapshots; attaching never changes campaign budget/status.
        con.execute("""CREATE TABLE IF NOT EXISTS media_plans (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            advertiser_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            campaign_id INTEGER REFERENCES campaigns(id) ON DELETE SET NULL,
            title TEXT NOT NULL CHECK(length(title) BETWEEN 2 AND 140),
            brief_json TEXT NOT NULL,
            plan_json TEXT NOT NULL,
            strategy TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'saved' CHECK(status IN ('saved','applied','archived')),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )""")
        con.execute("CREATE INDEX IF NOT EXISTS idx_media_plans_owner ON media_plans(advertiser_id,id DESC)")
        con.execute("CREATE INDEX IF NOT EXISTS idx_media_plans_campaign ON media_plans(campaign_id,id DESC)")
        # v2.3: advertiser-owned daily campaign metric snapshots. Values are user-entered, not inferred.
        con.execute("""CREATE TABLE IF NOT EXISTS campaign_metric_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            campaign_id INTEGER NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
            metric_date TEXT NOT NULL,
            spend INTEGER NOT NULL DEFAULT 0 CHECK(spend >= 0),
            impressions INTEGER NOT NULL DEFAULT 0 CHECK(impressions >= 0),
            clicks INTEGER NOT NULL DEFAULT 0 CHECK(clicks >= 0),
            conversions INTEGER NOT NULL DEFAULT 0 CHECK(conversions >= 0),
            revenue INTEGER CHECK(revenue IS NULL OR revenue >= 0),
            source TEXT NOT NULL DEFAULT 'manual' CHECK(source IN ('manual','import')),
            note TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(campaign_id, metric_date)
        )""")
        con.execute("CREATE INDEX IF NOT EXISTS idx_campaign_metrics_campaign_date ON campaign_metric_snapshots(campaign_id,metric_date)")
        # v2.6: partner promotion verification and privacy-preserving referral attribution.
        if "referral_code" not in _columns(con, "users"):
            con.execute("ALTER TABLE users ADD COLUMN referral_code TEXT")
        if "referred_by_supplier_id" not in _columns(con, "users"):
            con.execute("ALTER TABLE users ADD COLUMN referred_by_supplier_id INTEGER REFERENCES users(id) ON DELETE SET NULL")
        # Backfill stable codes for existing supplier accounts without exposing account identifiers.
        for supplier in con.execute("SELECT id FROM users WHERE role='supplier' AND (referral_code IS NULL OR referral_code='')").fetchall():
            import secrets
            code = secrets.token_urlsafe(9).replace('-', '').replace('_', '')[:12]
            con.execute("UPDATE users SET referral_code=? WHERE id=?", (code, supplier['id']))
        con.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_referral_code ON users(referral_code) WHERE referral_code IS NOT NULL")
        con.execute("CREATE INDEX IF NOT EXISTS idx_users_referred_by_supplier ON users(referred_by_supplier_id)")
        con.execute("""CREATE TABLE IF NOT EXISTS partner_promotion_sites (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            supplier_id INTEGER NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
            publication_url TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','approved','rejected')),
            review_note TEXT NOT NULL DEFAULT '',
            reviewed_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
            reviewed_at TEXT,
            submitted_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )""")
        con.execute("CREATE INDEX IF NOT EXISTS idx_partner_promotion_status ON partner_promotion_sites(status,submitted_at)")
        con.execute("""CREATE TABLE IF NOT EXISTS partner_referral_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            supplier_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            event_type TEXT NOT NULL CHECK(event_type IN ('click','registration')),
            referred_user_id INTEGER UNIQUE REFERENCES users(id) ON DELETE SET NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )""")
        con.execute("CREATE INDEX IF NOT EXISTS idx_partner_referral_supplier ON partner_referral_events(supplier_id,event_type,created_at)")
        # v2.8: immutable partner reward ledger, manual review flags and sandbox-only payout simulation.
        con.execute("INSERT OR IGNORE INTO app_settings(key,value) VALUES('partner_reward_amount','0')")
        con.execute("""CREATE TABLE IF NOT EXISTS partner_rewards (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            payment_intent_id INTEGER NOT NULL UNIQUE REFERENCES payment_intents(id),
            placement_id INTEGER NOT NULL REFERENCES placements(id),
            advertiser_id INTEGER NOT NULL REFERENCES users(id),
            supplier_id INTEGER NOT NULL REFERENCES users(id),
            amount INTEGER NOT NULL CHECK(amount > 0),
            status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','approved','reversed','paid_sandbox')),
            admin_note TEXT NOT NULL DEFAULT '',
            reviewed_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
            reviewed_at TEXT,
            paid_sandbox_at TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )""")
        con.execute("CREATE INDEX IF NOT EXISTS idx_partner_rewards_supplier ON partner_rewards(supplier_id,status,created_at DESC)")
        con.execute("CREATE INDEX IF NOT EXISTS idx_partner_rewards_status ON partner_rewards(status,created_at DESC)")
        con.execute("""CREATE TABLE IF NOT EXISTS partner_reward_flags (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            flag_key TEXT NOT NULL UNIQUE,
            reward_id INTEGER REFERENCES partner_rewards(id) ON DELETE SET NULL,
            supplier_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
            advertiser_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
            severity TEXT NOT NULL DEFAULT 'medium' CHECK(severity IN ('low','medium','high')),
            details TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open','reviewed','dismissed')),
            reviewed_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
            reviewed_at TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )""")
        con.execute("CREATE INDEX IF NOT EXISTS idx_partner_reward_flags_status ON partner_reward_flags(status,created_at DESC)")
        # v2.5: OAuth connection registry. Tokens are encrypted by the application before storage.
        con.execute("""CREATE TABLE IF NOT EXISTS ad_platform_connections (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            provider TEXT NOT NULL CHECK(provider IN ('yandex_direct')),
            external_account_id TEXT NOT NULL DEFAULT '',
            external_account_name TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'authorized_pending_access' CHECK(status IN ('authorized_pending_access','active','revoked','error')),
            token_ciphertext TEXT NOT NULL,
            token_expires_at TEXT,
            granted_scopes TEXT NOT NULL DEFAULT '',
            last_sync_at TEXT,
            last_error TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id,provider,external_account_id)
        )""")
        con.execute("CREATE INDEX IF NOT EXISTS idx_ad_platform_connections_user ON ad_platform_connections(user_id,provider,id DESC)")
        con.execute("""CREATE TABLE IF NOT EXISTS ad_platform_oauth_states (
            state_hash TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            provider TEXT NOT NULL CHECK(provider IN ('yandex_direct')),
            expires_at TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )""")
        con.execute("CREATE INDEX IF NOT EXISTS idx_ad_platform_oauth_expiry ON ad_platform_oauth_states(expires_at)")
        count = con.execute("SELECT COUNT(*) FROM inventory").fetchone()[0]
        if count == 0:
            con.executemany("""INSERT INTO inventory
                (name,channel,topic,geo,audience,price,formats,notes,demo)
                VALUES (?,?,?,?,?,?,?,?,1)""", SEED)
        con.commit()
