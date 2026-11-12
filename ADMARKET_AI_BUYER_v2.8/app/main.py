from pathlib import Path
import urllib.parse, urllib.request, urllib.error
import json, re, os, csv, io, hashlib, hmac, secrets, smtplib, logging
from fastapi import FastAPI, Request, Form, HTTPException, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse, PlainTextResponse, JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from pydantic import BaseModel, Field
from email.message import EmailMessage
from datetime import datetime, timedelta, timezone
from cryptography.fernet import Fernet, InvalidToken
from app.db import init_db, connect
from app.auth import (hash_password, verify_password, csrf_token, check_csrf,
                      current_user, require_user, bootstrap_admin, is_authorized_admin)
from app.ai import ask_ai
from app.planner import build_plan
from app.platform_trust import assess_account

ROOT = Path(__file__).resolve().parent
app = FastAPI(title="ADMARKET AI Media Buyer", version="2.8.0")
COMMISSION_RATE = 12.0  # Default only; actual configured value is read from app_settings.
app.add_middleware(SessionMiddleware,
    secret_key=os.getenv("SESSION_SECRET", "CHANGE-THIS-SESSION-SECRET-BEFORE-DEPLOYMENT"),
    session_cookie="admarket_session", max_age=60*60*24*7,
    same_site="lax", https_only=os.getenv("COOKIE_SECURE", "0") == "1")
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")
templates = Jinja2Templates(directory=ROOT / "templates")
templates.env.filters["from_json"] = json.loads

def integration_cipher():
    key = os.getenv("INTEGRATION_ENCRYPTION_KEY", "").strip()
    if not key:
        return None
    try:
        return Fernet(key.encode("ascii"))
    except Exception:
        raise RuntimeError("INTEGRATION_ENCRYPTION_KEY должен быть корректным Fernet-ключом")

def _integration_redirect_uri(request: Request) -> str:
    configured = os.getenv("YANDEX_DIRECT_REDIRECT_URI", "").strip()
    if configured:
        return configured
    return str(request.url_for("yandex_direct_oauth_callback"))

def _integration_config(request: Request):
    client_id = os.getenv("YANDEX_DIRECT_CLIENT_ID", "").strip()
    client_secret = os.getenv("YANDEX_DIRECT_CLIENT_SECRET", "").strip()
    try:
        cipher = integration_cipher()
    except RuntimeError:
        cipher = None
    redirect_uri = _integration_redirect_uri(request)
    ready = bool(client_id and client_secret and cipher and redirect_uri.startswith("https://"))
    return {"ready": ready, "client_id": client_id, "client_secret": client_secret, "cipher": cipher, "redirect_uri": redirect_uri}

def commission_rate(con=None):
    own = con is None
    if own: con = connect()
    try:
        row = con.execute("SELECT value FROM app_settings WHERE key='commission_rate'").fetchone()
        value = float(row["value"]) if row else 12.0
        return min(30.0, max(0.0, value))
    finally:
        if own: con.close()

def partner_reward_amount(con=None):
    own = con is None
    if own: con = connect()
    try:
        row = con.execute("SELECT value FROM app_settings WHERE key='partner_reward_amount'").fetchone()
        return max(0, min(100000, int(float(row['value'])))) if row else 0
    finally:
        if own: con.close()

def accrue_partner_reward(con, payment_id: int):
    """Create one test-only reward after a sandbox payment is released from escrow."""
    row = con.execute("""SELECT pi.id,pi.advertiser_id,pi.placement_id,pi.status,pi.mode,pi.provider,pi.escrow_status,
        ar.advertiser_id AS request_advertiser_id, u.referred_by_supplier_id,
        p.status AS placement_status, pps.status AS promotion_status
        FROM payment_intents pi JOIN placements p ON p.id=pi.placement_id
        JOIN request_responses rr ON rr.id=p.response_id JOIN advertiser_requests ar ON ar.id=rr.request_id
        JOIN users u ON u.id=pi.advertiser_id
        LEFT JOIN partner_promotion_sites pps ON pps.supplier_id=u.referred_by_supplier_id
        WHERE pi.id=?""", (payment_id,)).fetchone()
    if not row or row['mode'] != 'test' or row['provider'] != 'sandbox' or row['status'] != 'succeeded' or row['escrow_status'] != 'released' or row['placement_status'] != 'completed':
        return None
    supplier_id = row['referred_by_supplier_id']
    if not supplier_id or row['request_advertiser_id'] != row['advertiser_id'] or row['promotion_status'] != 'approved':
        return None
    attribution = con.execute("SELECT 1 FROM partner_referral_events WHERE supplier_id=? AND referred_user_id=? AND event_type='registration'", (supplier_id,row['advertiser_id'])).fetchone()
    if not attribution:
        return None
    amount = partner_reward_amount(con)
    if amount <= 0:
        return None
    cur = con.execute("INSERT OR IGNORE INTO partner_rewards(payment_intent_id,placement_id,advertiser_id,supplier_id,amount) VALUES(?,?,?,?,?)",
        (payment_id,row['placement_id'],row['advertiser_id'],supplier_id,amount))
    if not cur.rowcount:
        return con.execute("SELECT id FROM partner_rewards WHERE payment_intent_id=?",(payment_id,)).fetchone()['id']
    reward_id = cur.lastrowid
    # Conservative heuristic: several qualifying deals for one referred account in 24h get human review.
    rapid = con.execute("SELECT COUNT(*) FROM partner_rewards WHERE advertiser_id=? AND created_at >= datetime('now','-24 hours')",(row['advertiser_id'],)).fetchone()[0]
    if rapid >= 3:
        con.execute("INSERT OR IGNORE INTO partner_reward_flags(flag_key,reward_id,supplier_id,advertiser_id,severity,details) VALUES(?,?,?,?,?,?)",
            (f'rapid_deals:{row["advertiser_id"]}:{row["placement_id"]}',reward_id,supplier_id,row['advertiser_id'],'medium',
             f'У одного привлечённого рекламодателя зафиксировано {rapid} партнёрских начислений за 24 часа. Это только сигнал для ручной проверки, не доказательство нарушения.'))
    return reward_id

def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()

def moderate_text(entity_type: str, entity_id: int, fields: dict):
    """Transparent heuristic triage. It flags for a human; it never auto-approves or bans."""
    joined = " ".join(str(v or "") for v in fields.values())
    lowered = joined.lower()
    reasons = []
    score = 0
    if len(joined) > 5000: score += 15; reasons.append("слишком большой объём текста")
    if len(re.findall(r"https?://|www\.", lowered)) > 2: score += 30; reasons.append("много внешних ссылок")
    spam_terms = ("гарантированный доход", "100% гарантия", "накрутка", "купить подписчиков", "обход модерации")
    hits = [term for term in spam_terms if term in lowered]
    if hits: score += min(60, 30 + 10 * (len(hits)-1)); reasons.append("подозрительные рекламные обещания/услуги")
    if re.search(r"(.)\1{9,}", lowered): score += 20; reasons.append("повторяющиеся символы")
    score = min(100, score)
    decision = "review" if score >= 30 else "clear"
    with connect() as con:
        con.execute("INSERT OR REPLACE INTO moderation_records(entity_type,entity_id,score,decision,reasons) VALUES(?,?,?,?,?)",
                    (entity_type, entity_id, score, decision, "; ".join(reasons)))
        if decision == "review":
            con.execute("INSERT INTO risk_events(user_id,event_type,severity,details) VALUES(?,?,?,?)",
                        (fields.get("user_id"), "moderation_flag", "medium" if score < 60 else "high",
                         f"{entity_type} #{entity_id}: " + "; ".join(reasons)))
        con.commit()
    return score, decision, reasons

def supplier_quality(con, supplier_id: int):
    """Explainable 0-100 operational score; not a fraud verdict or automatic ban."""
    supplier = con.execute("SELECT supplier_verification FROM users WHERE id=? AND role='supplier'", (supplier_id,)).fetchone()
    if not supplier:
        return None
    ratings = con.execute("SELECT AVG(rating),COUNT(*) FROM reviews WHERE reviewee_id=?", (supplier_id,)).fetchone()
    sla = con.execute("SELECT COUNT(*),SUM(CASE WHEN status='met' THEN 1 ELSE 0 END),SUM(CASE WHEN status='breached' THEN 1 ELSE 0 END) FROM supplier_sla_cases WHERE supplier_id=?", (supplier_id,)).fetchone()
    complaints = con.execute("SELECT COUNT(*),SUM(CASE WHEN status='dismissed' THEN 1 ELSE 0 END),SUM(CASE WHEN status IN ('open','in_review') THEN 1 ELSE 0 END) FROM supplier_complaints WHERE supplier_id=?", (supplier_id,)).fetchone()
    review_count = int(ratings[1] or 0)
    rating_score = round((float(ratings[0]) / 5) * 100) if review_count else 60
    sla_count = int(sla[0] or 0)
    sla_score = round((int(sla[1] or 0) / sla_count) * 100) if sla_count else 75
    complaint_count = int(complaints[0] or 0)
    active_complaints = int(complaints[2] or 0)
    # Resolved/dismissed complaints are not treated as proven violations; open cases modestly reduce confidence only.
    complaint_score = max(40, 100 - min(60, active_complaints * 12))
    verification_score = 100 if supplier['supplier_verification'] == 'verified' else 65 if supplier['supplier_verification'] == 'pending' else 45
    # Bayesian-style neutral priors prevent one review or one SLA case from dominating.
    score = round(rating_score * .40 + sla_score * .30 + complaint_score * .20 + verification_score * .10)
    return {"supplier_id":supplier_id,"score":max(0,min(100,score)),"rating_score":rating_score,
            "sla_score":sla_score,"complaint_score":complaint_score,"verification_score":verification_score,
            "review_count":review_count,"sla_count":sla_count,"complaint_count":complaint_count,
            "active_complaints":active_complaints,"verification":supplier['supplier_verification']}

def scan_fraud_signals(con):
    """Conservative duplicate/implausible-listing triage; all signals require human review."""
    created = 0
    duplicates = con.execute("""SELECT MIN(id) AS first_id, GROUP_CONCAT(id) AS ids, COUNT(*) AS n
        FROM supplier_offers GROUP BY lower(trim(name)),lower(trim(channel)),lower(trim(topic)),lower(trim(geo)),audience,price
        HAVING COUNT(DISTINCT supplier_id)>1""").fetchall()
    for row in duplicates:
        ids = [int(x) for x in (row['ids'] or '').split(',') if x]
        for offer_id in ids:
            offer = con.execute("SELECT id,supplier_id,name FROM supplier_offers WHERE id=?",(offer_id,)).fetchone()
            if not offer: continue
            key = f"duplicate_offer:{offer_id}:{row['first_id']}"
            cur = con.execute("INSERT OR IGNORE INTO fraud_review_signals(signal_key,signal_type,severity,supplier_id,offer_id,details) VALUES(?,?,?,?,?,?)",
                (key,'cross_supplier_duplicate','medium',offer['supplier_id'],offer_id,
                 f"Похожее предложение опубликовано разными поставщиками (группа #{row['first_id']}); требуется проверить права и связь аккаунтов."))
            if cur.rowcount: created += 1
    odd = con.execute("SELECT id,supplier_id,name,audience,price FROM supplier_offers WHERE audience>50000000 OR price<100 OR price>20000000").fetchall()
    for offer in odd:
        reasons=[]
        if offer['audience']>50000000: reasons.append('аудитория больше 50 млн')
        if offer['price']<100: reasons.append('цена ниже 100 ₽')
        if offer['price']>20000000: reasons.append('цена выше 20 млн ₽')
        key=f"outlier_offer:{offer['id']}"
        cur=con.execute("INSERT OR IGNORE INTO fraud_review_signals(signal_key,signal_type,severity,supplier_id,offer_id,details) VALUES(?,?,?,?,?,?)",
            (key,'listing_outlier','low',offer['supplier_id'],offer['id'],f"Нетипичные параметры предложения «{offer['name']}»: {', '.join(reasons)}. Нужна ручная проверка."))
        if cur.rowcount: created += 1
    if created:
        con.execute("INSERT INTO risk_events(event_type,severity,details) VALUES('anti_fraud_scan','medium',?)",(f"Создано новых сигналов ручной проверки: {created}",))
    return created

def process_signed_sandbox_webhook(payload: dict, raw_body: bytes, signature: str, secret: str):
    if len(raw_body) > 65536:
        raise HTTPException(413, "Webhook слишком большой")
    expected = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    if not signature or not hmac.compare_digest(signature, expected):
        raise HTTPException(401, "Подпись webhook неверна")
    return process_sandbox_webhook(payload, raw_body)

def process_sandbox_webhook(payload: dict, raw_body: bytes):
    """Apply a signed, idempotent sandbox event. No external payment API is called."""
    event_id = str(payload.get("event_id", ""))
    event_type = str(payload.get("type", ""))
    payment_id = payload.get("payment_id")
    if not re.fullmatch(r"[A-Za-z0-9_.:-]{8,128}", event_id):
        raise HTTPException(400, "Некорректный event_id")
    if event_type not in ("payment.succeeded", "payment.failed"):
        raise HTTPException(400, "Неподдерживаемый тип события")
    try: payment_id = int(payment_id)
    except (TypeError, ValueError): raise HTTPException(400, "Некорректный payment_id")
    digest = hashlib.sha256(raw_body).hexdigest()
    with connect() as con:
        con.execute("BEGIN IMMEDIATE")
        old = con.execute("SELECT payload_hash FROM webhook_events WHERE event_id=?", (event_id,)).fetchone()
        if old:
            if old["payload_hash"] != digest: raise HTTPException(409, "event_id уже использован с другим содержимым")
            return "duplicate"
        row = con.execute("SELECT * FROM payment_intents WHERE id=? AND mode='test' AND provider='sandbox'", (payment_id,)).fetchone()
        if not row: raise HTTPException(404, "Sandbox-платёж не найден")
        if row["status"] != "created": raise HTTPException(409, "Платёж уже обработан")
        con.execute("INSERT INTO webhook_events(event_id,event_type,payload_hash) VALUES(?,?,?)", (event_id,event_type,digest))
        if event_type == "payment.failed":
            con.execute("UPDATE payment_intents SET status='failed',escrow_status='not_applicable' WHERE id=?", (payment_id,))
        else:
            # Funds are shown as held in sandbox clearing; supplier/platform balances are not credited yet.
            txid = con.execute("INSERT INTO ledger_transactions(payment_intent_id,kind,description) VALUES(?,'sandbox_payment','Sandbox: средства помещены в эскроу')", (payment_id,)).lastrowid
            entries = [('user',row['advertiser_id'],-row['amount']),('external',None,row['amount'])]
            if sum(x[2] for x in entries) != 0: raise HTTPException(500, "Нарушен баланс эскроу-проводок")
            con.executemany("INSERT INTO ledger_entries(transaction_id,account_type,user_id,amount) VALUES(?,?,?,?)", [(txid,*e) for e in entries])
            con.execute("UPDATE payment_intents SET status='succeeded',escrow_status='held',paid_at=CURRENT_TIMESTAMP WHERE id=?", (payment_id,))
            con.execute("INSERT INTO escrow_events(payment_intent_id,event_type,amount,note) VALUES(?,'funds_held',?,'Подписанное sandbox-событие')", (payment_id,row['amount']))
            con.execute("INSERT INTO risk_events(user_id,placement_id,event_type,severity,details) VALUES(?,?,?,?,?)", (row['advertiser_id'],row['placement_id'],'sandbox_payment_escrowed','low',f'Sandbox payment {payment_id} held in simulated escrow'))
        con.commit()
    return "processed"

def reconcile_financial_state(con=None):
    """Read-only consistency check for the sandbox ledger and escrow lifecycle."""
    own = con is None
    if own:
        con = connect()
    try:
        issues = []
        tx_rows = con.execute("""SELECT lt.id, lt.payment_intent_id, lt.kind,
            COALESCE(SUM(le.amount),0) AS total, COUNT(le.id) AS entries
            FROM ledger_transactions lt LEFT JOIN ledger_entries le ON le.transaction_id=lt.id
            GROUP BY lt.id ORDER BY lt.id""").fetchall()
        for row in tx_rows:
            if row['entries'] < 2 or row['total'] != 0:
                issues.append({"code":"unbalanced_transaction", "transaction_id":row['id'],
                               "payment_id":row['payment_intent_id'], "total":row['total'],
                               "entries":row['entries']})
        payments = con.execute("SELECT id,status,escrow_status,amount FROM payment_intents WHERE mode='test' ORDER BY id").fetchall()
        for payment in payments:
            ext = con.execute("""SELECT COALESCE(SUM(le.amount),0) FROM ledger_entries le
                JOIN ledger_transactions lt ON lt.id=le.transaction_id
                WHERE lt.payment_intent_id=? AND le.account_type='external'""", (payment['id'],)).fetchone()[0]
            expected = payment['amount'] if payment['escrow_status']=='held' else 0
            if payment['status']=='succeeded' and payment['escrow_status'] in ('held','released','refunded') and ext != expected:
                issues.append({"code":"escrow_clearing_mismatch", "payment_id":payment['id'],
                               "escrow_status":payment['escrow_status'], "expected_external_net":expected,
                               "actual_external_net":ext})
            if payment['status']=='refunded':
                refund = con.execute("SELECT 1 FROM refunds WHERE payment_intent_id=?", (payment['id'],)).fetchone()
                if not refund:
                    issues.append({"code":"missing_refund_record", "payment_id":payment['id']})
            if payment['escrow_status']=='held' and payment['status']!='succeeded':
                issues.append({"code":"invalid_escrow_state", "payment_id":payment['id']})
        return {"ok":not issues, "transactions_checked":len(tx_rows),
                "payments_checked":len(payments), "issues":issues}
    finally:
        if own:
            con.close()

def send_account_email(to_email: str, subject: str, link: str):
    host = os.getenv("SMTP_HOST", "").strip()
    if not host:
        logging.warning("ADMARKET email delivery is not configured; account link for %s: %s", to_email, link)
        return False
    msg = EmailMessage(); msg["Subject"] = subject
    msg["From"] = os.getenv("SMTP_FROM", os.getenv("SMTP_USER", "noreply@localhost"))
    msg["To"] = to_email
    msg.set_content(f"Открой ссылку для продолжения: {link}\nЕсли ты не запрашивал это действие, просто проигнорируй письмо.")
    with smtplib.SMTP(host, int(os.getenv("SMTP_PORT", "587")), timeout=15) as smtp:
        if os.getenv("SMTP_STARTTLS", "1") == "1": smtp.starttls()
        if os.getenv("SMTP_USER"): smtp.login(os.getenv("SMTP_USER"), os.getenv("SMTP_PASSWORD", ""))
        smtp.send_message(msg)
    return True

def issue_account_token(user_id: int, email: str, purpose: str):
    token = secrets.token_urlsafe(36)
    expires = (datetime.now(timezone.utc) + timedelta(hours=24 if purpose == "verify_email" else 1)).isoformat()
    with connect() as con:
        con.execute("UPDATE account_tokens SET used_at=CURRENT_TIMESTAMP WHERE user_id=? AND purpose=? AND used_at IS NULL", (user_id,purpose))
        con.execute("INSERT INTO account_tokens(user_id,purpose,token_hash,expires_at) VALUES(?,?,?,?)", (user_id,purpose,_token_hash(token),expires))
        con.commit()
    base = os.getenv("APP_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
    path = "verify-email" if purpose == "verify_email" else "reset-password"
    subject = "Подтверждение email — ADMARKET" if purpose == "verify_email" else "Сброс пароля — ADMARKET"
    return send_account_email(email, subject, f"{base}/{path}?token={token}")

def token_user(token: str, purpose: str):
    if not token or len(token) > 200: return None
    with connect() as con:
        row = con.execute("""SELECT t.id AS token_id,t.user_id,t.expires_at,u.email FROM account_tokens t
            JOIN users u ON u.id=t.user_id WHERE t.token_hash=? AND t.purpose=? AND t.used_at IS NULL""", (_token_hash(token),purpose)).fetchone()
        if not row: return None
        try: expiry = datetime.fromisoformat(row["expires_at"])
        except ValueError: return None
        if expiry <= datetime.now(timezone.utc): return None
        return dict(row)

@app.on_event("startup")
def startup():
    init_db()
    bootstrap_admin()

def page(request: Request, template: str, **context):
    user = current_user(request)
    notifications = []
    if user:
        with connect() as con:
            notifications = con.execute("SELECT * FROM notifications WHERE user_id=? ORDER BY id DESC LIMIT 12", (user["id"],)).fetchall()
    return templates.TemplateResponse(template, {"request": request, "user": user,
        "csrf": csrf_token(request), "notifications": notifications, **context})

def notify(con, user_id: int, kind: str, title: str, body: str = "", target_url: str = "/dashboard", placement_id=None, actor_id=None):
    con.execute("INSERT INTO notifications(user_id,kind,title,body,target_url) VALUES(?,?,?,?,?)",
                (user_id, kind, title[:160], body[:500], target_url[:300]))
    if placement_id is not None:
        con.execute("INSERT INTO activity_log(actor_id,placement_id,event_type,summary) VALUES(?,?,?,?)",
                    (actor_id, placement_id, kind, body[:500] or title[:500]))

def inventory_rows():
    """Demo inventory plus supplier offers approved by an admin. Approval is not a factual audit."""
    with connect() as con:
        base = [dict(r) for r in con.execute("SELECT * FROM inventory ORDER BY audience DESC").fetchall()]
        approved = con.execute("""SELECT so.*, u.company AS supplier_company
            FROM supplier_offers so JOIN users u ON u.id=so.supplier_id
            WHERE so.status='approved' AND u.is_active=1 AND u.supplier_verification='verified' ORDER BY so.audience DESC""").fetchall()
    for row in approved:
        item = dict(row)
        base.append({
            "id": f"supplier-{item['id']}", "name": item["name"], "channel": item["channel"],
            "topic": item["topic"], "geo": item["geo"], "audience": item["audience"],
            "price": item["price"], "formats": item["formats"],
            "notes": "Предложение поставщика, допущенное администратором в каталог. Независимая проверка характеристик и доступности не проводилась.",
            "demo": 0, "supplier_id": item["supplier_id"], "supplier_company": item["supplier_company"]
        })
    return sorted(base, key=lambda x: int(x.get("audience") or 0), reverse=True)

@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    inventory = inventory_rows()
    channels = sorted({row["channel"] for row in inventory})
    return page(request, "index.html", inventory=inventory, channels=channels, commission=12)

@app.post("/recommend", response_class=HTMLResponse)
def recommend(request: Request, goal: str = Form(...), geo: str = Form(...), budget: int = Form(...), channel: str = Form("")):
    if budget < 1000 or budget > 100000000:
        raise HTTPException(400, "Бюджет должен быть от 1 000 до 100 000 000 ₽")
    rows = inventory_rows()
    terms = [x.strip().lower() for x in goal.split() if len(x.strip()) > 3]
    ranked = []
    for row in rows:
        hay = (row["name"] + " " + row["topic"] + " " + row["channel"]).lower()
        score = sum(3 for term in terms if term in hay)
        if geo.lower() in row["geo"].lower() or row["geo"] == "Россия": score += 2
        if channel and channel == row["channel"]: score += 2
        if row["price"] <= budget: score += 2
        if score and row["price"] <= budget: ranked.append((score, row))
    ranked.sort(key=lambda pair: (pair[0], pair[1]["audience"]), reverse=True)
    selected = [row for _, row in ranked[:6]]
    return page(request, "recommendations.html", items=selected, goal=goal, geo=geo, budget=budget)

@app.get("/health")
def health():
    return {"status":"ok", "app":"ADMARKET AI Media Buyer", "version":"2.8.0", "payment_mode":"sandbox-only", "escrow":"simulated"}

@app.get("/integrations", response_class=HTMLResponse)
def integrations_page(request: Request):
    user = require_user(request, "advertiser")
    cfg = _integration_config(request)
    with connect() as con:
        connections = con.execute("SELECT id,provider,external_account_id,external_account_name,status,token_expires_at,last_sync_at,last_error,created_at FROM ad_platform_connections WHERE user_id=? ORDER BY id DESC", (user["id"],)).fetchall()
    return page(request, "integrations.html", connections=connections, yandex_ready=cfg["ready"], redirect_uri=cfg["redirect_uri"])

@app.post("/integrations/yandex-direct/connect")
def yandex_direct_oauth_start(request: Request, csrf: str = Form(...)):
    check_csrf(request, csrf); user = require_user(request, "advertiser")
    cfg = _integration_config(request)
    if not cfg["ready"]:
        raise HTTPException(503, "OAuth не настроен: добавьте YANDEX_DIRECT_CLIENT_ID, YANDEX_DIRECT_CLIENT_SECRET, INTEGRATION_ENCRYPTION_KEY и HTTPS redirect URI")
    state = secrets.token_urlsafe(40)
    state_hash = hashlib.sha256(state.encode()).hexdigest()
    expiry = (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()
    with connect() as con:
        con.execute("DELETE FROM ad_platform_oauth_states WHERE expires_at < ?", (datetime.now(timezone.utc).isoformat(),))
        con.execute("INSERT INTO ad_platform_oauth_states(state_hash,user_id,provider,expires_at) VALUES(?,?,'yandex_direct',?)", (state_hash,user["id"],expiry))
        con.commit()
    params = urllib.parse.urlencode({"response_type":"code", "client_id":cfg["client_id"], "redirect_uri":cfg["redirect_uri"], "state":state})
    return RedirectResponse("https://oauth.yandex.com/authorize?" + params, status_code=303)

@app.get("/integrations/yandex-direct/callback", name="yandex_direct_oauth_callback")
def yandex_direct_oauth_callback(request: Request, code: str = "", state: str = "", error: str = ""):
    if error:
        raise HTTPException(400, "Авторизация Яндекс Директа отменена или отклонена")
    if not code or len(code)>2048 or not state or len(state)>256:
        raise HTTPException(400, "Некорректный ответ OAuth")
    cfg = _integration_config(request)
    if not cfg["ready"]:
        raise HTTPException(503, "OAuth-интеграция не настроена на сервере")
    state_hash = hashlib.sha256(state.encode()).hexdigest()
    with connect() as con:
        row = con.execute("SELECT user_id,expires_at FROM ad_platform_oauth_states WHERE state_hash=? AND provider='yandex_direct'", (state_hash,)).fetchone()
        if not row:
            raise HTTPException(400, "OAuth state недействителен или уже использован")
        try: expiry = datetime.fromisoformat(row["expires_at"])
        except ValueError: expiry = datetime.min.replace(tzinfo=timezone.utc)
        if expiry <= datetime.now(timezone.utc):
            con.execute("DELETE FROM ad_platform_oauth_states WHERE state_hash=?", (state_hash,)); con.commit()
            raise HTTPException(400, "Срок OAuth state истёк. Начните подключение заново")
        owner_id = row["user_id"]
        con.execute("DELETE FROM ad_platform_oauth_states WHERE state_hash=?", (state_hash,)); con.commit()
    payload = urllib.parse.urlencode({"grant_type":"authorization_code", "code":code, "client_id":cfg["client_id"], "client_secret":cfg["client_secret"], "redirect_uri":cfg["redirect_uri"]}).encode()
    req = urllib.request.Request("https://oauth.yandex.com/token", data=payload, headers={"Content-Type":"application/x-www-form-urlencoded", "Accept":"application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=12) as response:
            token_data = json.loads(response.read(65537).decode("utf-8"))
    except Exception:
        logging.exception("Yandex OAuth token exchange failed")
        raise HTTPException(502, "Не удалось завершить обмен OAuth-кода. Проверьте настройки приложения и попробуйте снова")
    access_token = token_data.get("access_token") if isinstance(token_data,dict) else None
    if not isinstance(access_token,str) or not access_token or len(access_token)>8192:
        raise HTTPException(502, "OAuth-провайдер не вернул корректный access token")
    encrypted = cfg["cipher"].encrypt(access_token.encode()).decode("ascii")
    expires = None
    if token_data.get("expires_in") is not None:
        try: expires=(datetime.now(timezone.utc)+timedelta(seconds=max(0,int(token_data["expires_in"])))).isoformat()
        except (ValueError,TypeError): expires=None
    with connect() as con:
        con.execute("INSERT INTO ad_platform_connections(user_id,provider,status,token_ciphertext,token_expires_at,granted_scopes) VALUES(?,'yandex_direct','authorized_pending_access',?,?,?) ON CONFLICT(user_id,provider,external_account_id) DO UPDATE SET status='authorized_pending_access',token_ciphertext=excluded.token_ciphertext,token_expires_at=excluded.token_expires_at,last_error='',updated_at=CURRENT_TIMESTAMP", (owner_id,encrypted,expires,str(token_data.get("scope") or "")[:500]))
        con.commit()
    return RedirectResponse("/integrations?connected=yandex#yandex-direct", status_code=303)

@app.post("/integrations/{connection_id}/disconnect")
def disconnect_ad_platform(request: Request, connection_id: int, csrf: str = Form(...)):
    check_csrf(request, csrf); user = require_user(request, "advertiser")
    with connect() as con:
        cur = con.execute("DELETE FROM ad_platform_connections WHERE id=? AND user_id=?", (connection_id,user["id"]))
        con.commit()
    if not cur.rowcount: raise HTTPException(404, "Подключение не найдено")
    return RedirectResponse("/integrations?disconnected=1", status_code=303)

@app.get("/api/integrations")
def integrations_status_api(request: Request):
    user = require_user(request, "advertiser")
    cfg = _integration_config(request)
    with connect() as con:
        rows=con.execute("SELECT provider,status,COUNT(*) AS accounts FROM ad_platform_connections WHERE user_id=? GROUP BY provider,status",(user["id"],)).fetchall()
    return {"version":"2.8.0","providers":{"yandex_direct":{"oauth_configured":cfg["ready"],"connections":[dict(r) for r in rows if r["provider"]=="yandex_direct"],"sync_enabled":False},"vk_ads":{"oauth_configured":False,"connections":[],"sync_enabled":False,"reason":"Официальный flow и права доступа требуют отдельной проверки перед включением"}},"note":"OAuth-подключение Яндекс Директа ещё не подтверждает допуск приложения к API. Синхронизация и чтение статистики не выполняются в текущем релизе."}

@app.get("/r/{referral_code}")
def partner_referral_redirect(request: Request, referral_code: str):
    """Count one partner visit per browser session; no IP address or user-agent is stored."""
    code = referral_code.strip()[:32]
    with connect() as con:
        supplier = con.execute("SELECT id FROM users WHERE referral_code=? AND role='supplier' AND is_active=1", (code,)).fetchone()
        if not supplier:
            raise HTTPException(404, "Партнёрская ссылка не найдена")
        seen = request.session.get("partner_clicked_codes", [])
        if code not in seen:
            con.execute("INSERT INTO partner_referral_events(supplier_id,event_type) VALUES(?,'click')", (supplier['id'],))
            request.session["partner_clicked_codes"] = (seen + [code])[-30:]
        # First-touch attribution is retained for the current session.
        if not request.session.get("partner_referral_code"):
            request.session["partner_referral_code"] = code
        con.commit()
    return RedirectResponse("/register", status_code=302)

@app.get("/register", response_class=HTMLResponse)
def register_page(request: Request):
    if current_user(request): return RedirectResponse("/dashboard", status_code=303)
    return page(request, "auth.html", mode="register", error="")

@app.post("/register")
def register(request: Request, csrf: str = Form(...), email: str = Form(...), password: str = Form(...),
             role: str = Form(...), name: str = Form(...), company: str = Form(""), promotion_url: str = Form("")):
    check_csrf(request, csrf)
    email = email.strip().lower(); name = name.strip(); company = company.strip(); promotion_url = promotion_url.strip()[:1000]
    if role not in ("advertiser", "supplier"):
        raise HTTPException(400, "Недопустимая роль")
    if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email) or len(email) > 254:
        raise HTTPException(400, "Укажи корректный email")
    if len(password) < 10 or len(password) > 128:
        raise HTTPException(400, "Пароль должен содержать от 10 до 128 символов")
    if not name or len(name) > 100 or len(company) > 160:
        raise HTTPException(400, "Проверь имя и название компании")
    if role == "supplier":
        from urllib.parse import urlparse
        parsed = urlparse(promotion_url)
        if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
            raise HTTPException(400, "Для поставщика нужна HTTPS-ссылка на публикацию с рекламой ADMARKET")
    referral_code = request.session.get("partner_referral_code", "")
    with connect() as con:
        try:
            referred_by = None
            if role == "advertiser" and referral_code:
                referrer = con.execute("SELECT id FROM users WHERE referral_code=? AND role='supplier' AND is_active=1", (referral_code,)).fetchone()
                referred_by = referrer['id'] if referrer else None
            cur = con.execute("INSERT INTO users(email,password_hash,role,name,company,email_verified,referral_code,referred_by_supplier_id) VALUES(?,?,?,?,?,0,?,?)",
                (email, hash_password(password), role, name, company, __import__('secrets').token_urlsafe(9).replace('-', '').replace('_', '')[:12] if role == 'supplier' else None, referred_by))
            user_id = cur.lastrowid
            if role == "supplier":
                con.execute("INSERT INTO partner_promotion_sites(supplier_id,publication_url,status) VALUES(?,?,'pending')", (user_id, promotion_url))
            elif referred_by:
                con.execute("INSERT OR IGNORE INTO partner_referral_events(supplier_id,event_type,referred_user_id) VALUES(?,'registration',?)", (referred_by, user_id))
            con.commit()
        except Exception as exc:
            if "UNIQUE" in str(exc).upper(): raise HTTPException(409, "Аккаунт с таким email уже существует")
            raise
    if role == "advertiser":
        request.session.pop("partner_referral_code", None)
    sent = issue_account_token(user_id, email, "verify_email")
    return RedirectResponse("/login?registered=1" if sent else "/login?mail_setup=1", status_code=303)

@app.get("/verify-email", response_class=HTMLResponse)
def verify_email_page(request: Request, token: str = ""):
    record = token_user(token, "verify_email")
    if not record: return HTMLResponse("<h2>Ссылка недействительна или истекла.</h2><a href='/login'>Войти</a>", status_code=400)
    with connect() as con:
        con.execute("UPDATE users SET email_verified=1 WHERE id=?", (record["user_id"],))
        con.execute("UPDATE account_tokens SET used_at=CURRENT_TIMESTAMP WHERE id=?", (record["token_id"],)); con.commit()
    return HTMLResponse("<h2>Email подтверждён.</h2><a href='/login'>Перейти ко входу</a>")

@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    if current_user(request): return RedirectResponse("/dashboard", status_code=303)
    return page(request, "auth.html", mode="login", error="")

@app.post("/login")
def login(request: Request, csrf: str = Form(...), email: str = Form(...), password: str = Form(...)):
    check_csrf(request, csrf)
    with connect() as con:
        row = con.execute("SELECT id,password_hash,is_active,email_verified FROM users WHERE email=?", (email.strip().lower(),)).fetchone()
    if not row or not row["is_active"] or not verify_password(password, row["password_hash"]):
        raise HTTPException(401, "Неверный email или пароль")
    if not row["email_verified"]:
        raise HTTPException(403, "Сначала подтверди email по ссылке из письма. Можно запросить новое письмо на /resend-verification.")
    request.session.clear(); request.session["user_id"] = row["id"]; csrf_token(request)
    return RedirectResponse("/dashboard", status_code=303)

@app.get("/forgot-password", response_class=HTMLResponse)
def forgot_password_page(request: Request):
    csrf = csrf_token(request)
    return HTMLResponse(f"""<html lang='ru'><meta charset='utf-8'><title>Восстановление пароля</title><body><h2>Восстановление пароля</h2><form method='post'><input type='hidden' name='csrf' value='{csrf}'><input name='email' type='email' required placeholder='Email'><button>Отправить ссылку</button></form><a href='/login'>Назад ко входу</a></body></html>""")

@app.post("/forgot-password")
def forgot_password(request: Request, csrf: str = Form(...), email: str = Form(...)):
    check_csrf(request, csrf); email = email.strip().lower()
    with connect() as con:
        recent = con.execute("SELECT COUNT(*) FROM security_events WHERE email=? AND event_type='reset' AND created_at >= datetime('now','-1 hour')", (email,)).fetchone()[0]
        row = con.execute("SELECT id,email FROM users WHERE email=? AND is_active=1", (email,)).fetchone()
        con.execute("INSERT INTO security_events(email,event_type) VALUES(?,'reset')", (email,)); con.commit()
    if row and recent < 3: issue_account_token(row["id"], row["email"], "reset_password")
    return HTMLResponse("<h2>Если аккаунт существует, письмо со ссылкой будет отправлено.</h2><a href='/login'>Вернуться ко входу</a>")

@app.get("/reset-password", response_class=HTMLResponse)
def reset_password_page(request: Request, token: str = ""):
    if not token_user(token, "reset_password"): return HTMLResponse("<h2>Ссылка недействительна или истекла.</h2>", status_code=400)
    csrf = csrf_token(request)
    return HTMLResponse(f"""<html lang='ru'><meta charset='utf-8'><title>Новый пароль</title><body><h2>Задай новый пароль</h2><form method='post'><input type='hidden' name='csrf' value='{csrf}'><input type='hidden' name='token' value='{token}'><input name='password' type='password' minlength='12' maxlength='128' required><button>Сохранить пароль</button></form></body></html>""")

@app.post("/reset-password")
def reset_password(request: Request, csrf: str = Form(...), token: str = Form(...), password: str = Form(...)):
    check_csrf(request, csrf); record = token_user(token, "reset_password")
    if not record: raise HTTPException(400, "Ссылка недействительна или истекла")
    if len(password) < 12 or len(password) > 128: raise HTTPException(400, "Пароль должен содержать 12–128 символов")
    with connect() as con:
        con.execute("UPDATE users SET password_hash=? WHERE id=?", (hash_password(password),record["user_id"]))
        con.execute("UPDATE account_tokens SET used_at=CURRENT_TIMESTAMP WHERE id=?", (record["token_id"],))
        con.commit()
    return HTMLResponse("<h2>Пароль обновлён. Теперь можно войти.</h2><a href='/login'>Войти</a>")

@app.get("/resend-verification", response_class=HTMLResponse)
def resend_verification_page(request: Request):
    csrf = csrf_token(request)
    return HTMLResponse(f"""<html lang='ru'><meta charset='utf-8'><title>Подтверждение email</title><body><h2>Отправить письмо повторно</h2><form method='post'><input type='hidden' name='csrf' value='{csrf}'><input name='email' type='email' required placeholder='Email'><button>Отправить</button></form></body></html>""")

@app.post("/resend-verification")
def resend_verification(request: Request, csrf: str = Form(...), email: str = Form(...)):
    check_csrf(request, csrf); email=email.strip().lower()
    with connect() as con:
        recent=con.execute("SELECT COUNT(*) FROM security_events WHERE email=? AND event_type='verify' AND created_at >= datetime('now','-1 hour')",(email,)).fetchone()[0]
        row=con.execute("SELECT id,email,email_verified FROM users WHERE email=?",(email,)).fetchone()
        con.execute("INSERT INTO security_events(email,event_type) VALUES(?,'verify')",(email,)); con.commit()
    if row and not row["email_verified"] and recent < 3: issue_account_token(row["id"],row["email"],"verify_email")
    return HTMLResponse("<h2>Если аккаунт ожидает подтверждения, письмо будет отправлено.</h2><a href='/login'>Ко входу</a>")

@app.post("/logout")
def logout(request: Request, csrf: str = Form(...)):
    check_csrf(request, csrf); request.session.clear()
    return RedirectResponse("/", status_code=303)

@app.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request):
    user = current_user(request)
    if not user: return RedirectResponse("/login", status_code=303)
    with connect() as con:
        available_requests = []; responses = []; request_responses = []; placements = []; messages = []; terms = []; activities = []; campaigns = []
        partner_site = None; partner_clicks = 0; partner_registrations = 0
        partner_7d_clicks = 0; partner_7d_registrations = 0
        partner_30d_clicks = 0; partner_30d_registrations = 0; partner_daily_rows = []
        partner_reward_rows = []; partner_reward_totals = {"pending":0,"approved":0,"paid_sandbox":0}
        if user["role"] == "advertiser":
            campaigns = con.execute("SELECT * FROM campaigns WHERE advertiser_id=? ORDER BY id DESC", (user["id"],)).fetchall()
            rows = con.execute("SELECT * FROM advertiser_requests WHERE advertiser_id=? ORDER BY id DESC", (user["id"],)).fetchall()
            legacy = con.execute("SELECT * FROM orders WHERE user_id=? ORDER BY id DESC", (user["id"],)).fetchall()
            offers = []
            request_responses = con.execute("""SELECT rr.*, ar.title AS request_title, u.company AS supplier_company, u.name AS supplier_name
                FROM request_responses rr JOIN advertiser_requests ar ON ar.id=rr.request_id
                JOIN users u ON u.id=rr.supplier_id WHERE ar.advertiser_id=? ORDER BY rr.id DESC""", (user["id"],)).fetchall()
            placements = con.execute("""SELECT p.*, rr.id AS response_id, rr.proposed_price, ar.title AS request_title,
                u.name AS supplier_name, u.company AS supplier_company FROM placements p
                JOIN request_responses rr ON rr.id=p.response_id JOIN advertiser_requests ar ON ar.id=rr.request_id
                JOIN users u ON u.id=rr.supplier_id WHERE ar.advertiser_id=? ORDER BY p.id DESC""", (user["id"],)).fetchall()
            messages = con.execute("""SELECT m.*, rr.id AS response_id, ar.title AS request_title,
                u.name AS sender_name, u.role AS sender_role FROM messages m
                JOIN request_responses rr ON rr.id=m.response_id JOIN advertiser_requests ar ON ar.id=rr.request_id
                JOIN users u ON u.id=m.sender_id WHERE ar.advertiser_id=? AND rr.status='accepted'
                ORDER BY m.id ASC""", (user["id"],)).fetchall()
        elif user["role"] == "supplier":
            rows = con.execute("SELECT * FROM supplier_offers WHERE supplier_id=? ORDER BY id DESC", (user["id"],)).fetchall()
            partner_site = con.execute("SELECT * FROM partner_promotion_sites WHERE supplier_id=?", (user["id"],)).fetchone()
            partner_clicks = con.execute("SELECT COUNT(*) FROM partner_referral_events WHERE supplier_id=? AND event_type='click'", (user["id"],)).fetchone()[0]
            partner_registrations = con.execute("SELECT COUNT(*) FROM partner_referral_events WHERE supplier_id=? AND event_type='registration'", (user["id"],)).fetchone()[0]
            partner_7d = con.execute("SELECT SUM(CASE WHEN event_type='click' THEN 1 ELSE 0 END) clicks,SUM(CASE WHEN event_type='registration' THEN 1 ELSE 0 END) registrations FROM partner_referral_events WHERE supplier_id=? AND created_at >= datetime('now','-7 days')", (user["id"],)).fetchone()
            partner_30d = con.execute("SELECT SUM(CASE WHEN event_type='click' THEN 1 ELSE 0 END) clicks,SUM(CASE WHEN event_type='registration' THEN 1 ELSE 0 END) registrations FROM partner_referral_events WHERE supplier_id=? AND created_at >= datetime('now','-30 days')", (user["id"],)).fetchone()
            partner_7d_clicks = partner_7d['clicks'] or 0; partner_7d_registrations = partner_7d['registrations'] or 0
            partner_30d_clicks = partner_30d['clicks'] or 0; partner_30d_registrations = partner_30d['registrations'] or 0
            partner_daily_rows = con.execute("SELECT date(created_at) AS day,SUM(CASE WHEN event_type='click' THEN 1 ELSE 0 END) AS clicks,SUM(CASE WHEN event_type='registration' THEN 1 ELSE 0 END) AS registrations FROM partner_referral_events WHERE supplier_id=? AND created_at >= datetime('now','-13 days') GROUP BY date(created_at) ORDER BY day DESC", (user["id"],)).fetchall()
            partner_reward_rows = con.execute("SELECT pr.* FROM partner_rewards pr WHERE pr.supplier_id=? ORDER BY pr.id DESC LIMIT 100", (user["id"],)).fetchall()
            partner_reward_totals = con.execute("SELECT COALESCE(SUM(CASE WHEN status='pending' THEN amount ELSE 0 END),0) pending,COALESCE(SUM(CASE WHEN status='approved' THEN amount ELSE 0 END),0) approved,COALESCE(SUM(CASE WHEN status='paid_sandbox' THEN amount ELSE 0 END),0) paid_sandbox FROM partner_rewards WHERE supplier_id=?", (user["id"],)).fetchone()
            platform_accounts = con.execute("SELECT pa.*, (SELECT COUNT(*) FROM platform_metric_snapshots ms WHERE ms.account_id=pa.id) AS snapshot_count FROM platform_accounts pa WHERE pa.supplier_id=? ORDER BY pa.id DESC", (user["id"],)).fetchall()
            legacy = []; offers = rows
            available_requests = con.execute("""SELECT ar.* FROM advertiser_requests ar
                WHERE ar.status='Открыта' AND NOT EXISTS (
                    SELECT 1 FROM request_responses rr WHERE rr.request_id=ar.id AND rr.supplier_id=?
                ) ORDER BY ar.id DESC""", (user["id"],)).fetchall()
            responses = con.execute("""SELECT rr.*, ar.title AS request_title FROM request_responses rr
                JOIN advertiser_requests ar ON ar.id=rr.request_id WHERE rr.supplier_id=? ORDER BY rr.id DESC""", (user["id"],)).fetchall()
            placements = con.execute("""SELECT p.*, rr.id AS response_id, rr.proposed_price, ar.title AS request_title,
                ar.advertiser_id, u.name AS advertiser_name, u.company AS advertiser_company
                FROM placements p JOIN request_responses rr ON rr.id=p.response_id
                JOIN advertiser_requests ar ON ar.id=rr.request_id JOIN users u ON u.id=ar.advertiser_id
                WHERE rr.supplier_id=? ORDER BY p.id DESC""", (user["id"],)).fetchall()
            messages = con.execute("""SELECT m.*, rr.id AS response_id, ar.title AS request_title,
                u.name AS sender_name, u.role AS sender_role FROM messages m
                JOIN request_responses rr ON rr.id=m.response_id JOIN advertiser_requests ar ON ar.id=rr.request_id
                JOIN users u ON u.id=m.sender_id WHERE rr.supplier_id=? AND rr.status='accepted'
                ORDER BY m.id ASC""", (user["id"],)).fetchall()
        else:
            rows = []; legacy = []; offers = []; platform_accounts = []
        if user["role"] in ("advertiser", "supplier"):
            placement_ids = [r["id"] for r in placements]
            if placement_ids:
                marks = ",".join("?" for _ in placement_ids)
                terms = con.execute(f"SELECT * FROM placement_terms WHERE placement_id IN ({marks}) ORDER BY placement_id,version DESC", placement_ids).fetchall()
                activities = con.execute(f"SELECT * FROM activity_log WHERE placement_id IN ({marks}) ORDER BY id DESC", placement_ids).fetchall()
        # v2.0: role-scoped workspace overview. All figures are derived from existing records;
        # no schema changes or synthetic financial balances are introduced.
        workspace_stats = {}
        workspace_actions = []
        unread_count = con.execute("SELECT COUNT(*) FROM notifications WHERE user_id=? AND is_read=0", (user["id"],)).fetchone()[0]
        if user["role"] == "advertiser":
            workspace_stats = {
                "primary_label": "Мои заявки",
                "primary_value": con.execute("SELECT COUNT(*) FROM advertiser_requests WHERE advertiser_id=?", (user["id"],)).fetchone()[0],
                "secondary_label": "Ожидают решения",
                "secondary_value": con.execute("SELECT COUNT(*) FROM request_responses rr JOIN advertiser_requests ar ON ar.id=rr.request_id WHERE ar.advertiser_id=? AND rr.status='pending'", (user["id"],)).fetchone()[0],
                "active_label": "Активные размещения",
                "active_value": con.execute("SELECT COUNT(*) FROM placements p JOIN request_responses rr ON rr.id=p.response_id JOIN advertiser_requests ar ON ar.id=rr.request_id WHERE ar.advertiser_id=? AND p.status IN ('awaiting_supplier','confirmed','published')", (user["id"],)).fetchone()[0],
                "completed_label": "Завершено",
                "completed_value": con.execute("SELECT COUNT(*) FROM placements p JOIN request_responses rr ON rr.id=p.response_id JOIN advertiser_requests ar ON ar.id=rr.request_id WHERE ar.advertiser_id=? AND p.status='completed'", (user["id"],)).fetchone()[0],
                "unread": unread_count,
            }
            if workspace_stats["secondary_value"]:
                workspace_actions.append({"title":"Рассмотреть отклики поставщиков", "description":f"{workspace_stats['secondary_value']} предложений ждут решения", "url":"#responses", "kind":"attention"})
            if workspace_stats["active_value"]:
                workspace_actions.append({"title":"Проверить ход размещений", "description":f"{workspace_stats['active_value']} размещений ещё в работе", "url":"#placements", "kind":"normal"})
            if not rows:
                workspace_actions.append({"title":"Создать первую рекламную задачу", "description":"Опиши цель, бюджет и географию кампании", "url":"#new-request", "kind":"normal"})
            if unread_count:
                workspace_actions.append({"title":"Прочитать уведомления", "description":f"Непрочитанных: {unread_count}", "url":"#notifications", "kind":"attention"})
        elif user["role"] == "supplier":
            workspace_stats = {
                "primary_label": "Мои предложения",
                "primary_value": con.execute("SELECT COUNT(*) FROM supplier_offers WHERE supplier_id=?", (user["id"],)).fetchone()[0],
                "secondary_label": "Ожидают модерации",
                "secondary_value": con.execute("SELECT COUNT(*) FROM supplier_offers WHERE supplier_id=? AND status='pending'", (user["id"],)).fetchone()[0],
                "active_label": "Активные размещения",
                "active_value": con.execute("SELECT COUNT(*) FROM placements p JOIN request_responses rr ON rr.id=p.response_id WHERE rr.supplier_id=? AND p.status IN ('awaiting_supplier','confirmed','published')", (user["id"],)).fetchone()[0],
                "completed_label": "Завершено",
                "completed_value": con.execute("SELECT COUNT(*) FROM placements p JOIN request_responses rr ON rr.id=p.response_id WHERE rr.supplier_id=? AND p.status='completed'", (user["id"],)).fetchone()[0],
                "unread": unread_count,
            }
            if available_requests:
                workspace_actions.append({"title":"Найти новые рекламные задачи", "description":f"Доступно заявок: {len(available_requests)}", "url":"#opportunities", "kind":"normal"})
            if workspace_stats["active_value"]:
                workspace_actions.append({"title":"Обновить статусы размещений", "description":f"В работе: {workspace_stats['active_value']}", "url":"#placements", "kind":"attention"})
            if workspace_stats["secondary_value"]:
                workspace_actions.append({"title":"Проверить предложения на модерации", "description":f"Ожидают проверки: {workspace_stats['secondary_value']}", "url":"#offers", "kind":"normal"})
            if not platform_accounts:
                workspace_actions.append({"title":"Добавить рекламную площадку", "description":"Заполни профиль и добавь статистику", "url":"#platform-accounts", "kind":"normal"})
            if unread_count:
                workspace_actions.append({"title":"Прочитать уведомления", "description":f"Непрочитанных: {unread_count}", "url":"#notifications", "kind":"attention"})
        else:
            workspace_stats = {
                "primary_label": "Пользователи",
                "primary_value": con.execute("SELECT COUNT(*) FROM users").fetchone()[0],
                "secondary_label": "Предложения на модерации",
                "secondary_value": con.execute("SELECT COUNT(*) FROM supplier_offers WHERE status='pending'").fetchone()[0],
                "active_label": "Открытые споры",
                "active_value": con.execute("SELECT COUNT(*) FROM disputes WHERE status IN ('open','in_review')").fetchone()[0],
                "completed_label": "Сигналы риска",
                "completed_value": con.execute("SELECT COUNT(*) FROM risk_events WHERE status='open'").fetchone()[0],
                "unread": unread_count,
            }
            if is_authorized_admin(user):
                workspace_actions.append({"title":"Открыть защищённую админ-панель", "description":"Модерация, споры, финансы и контроль качества", "url":"/admin", "kind":"attention"})
        if not workspace_actions:
            workspace_actions.append({"title":"Рабочее пространство готово", "description":"Новых обязательных действий пока нет", "url":"/analytics", "kind":"normal"})
        finance = []
        if user["role"] == "advertiser":
            finance = con.execute("""SELECT fe.*,pt.version,ar.title AS request_title,u.name AS supplier_name,u.company AS supplier_company
                FROM financial_estimates fe JOIN placement_terms pt ON pt.id=fe.terms_id
                JOIN placements p ON p.id=fe.placement_id JOIN request_responses rr ON rr.id=p.response_id
                JOIN advertiser_requests ar ON ar.id=rr.request_id JOIN users u ON u.id=fe.supplier_id
                WHERE fe.advertiser_id=? ORDER BY fe.id DESC""", (user["id"],)).fetchall()
        elif user["role"] == "supplier":
            finance = con.execute("""SELECT fe.*,pt.version,ar.title AS request_title,u.name AS advertiser_name,u.company AS advertiser_company
                FROM financial_estimates fe JOIN placement_terms pt ON pt.id=fe.terms_id
                JOIN placements p ON p.id=fe.placement_id JOIN request_responses rr ON rr.id=p.response_id
                JOIN advertiser_requests ar ON ar.id=rr.request_id JOIN users u ON u.id=fe.advertiser_id
                WHERE fe.supplier_id=? ORDER BY fe.id DESC""", (user["id"],)).fetchall()
        ledger = con.execute("SELECT COALESCE(SUM(amount),0) FROM ledger_entries WHERE account_type='user' AND user_id=?", (user["id"],)).fetchone()[0]
        transactions = con.execute("""SELECT lt.*,pi.amount,pi.status AS payment_status FROM ledger_transactions lt
            LEFT JOIN payment_intents pi ON pi.id=lt.payment_intent_id
            WHERE lt.id IN (SELECT transaction_id FROM ledger_entries WHERE account_type='user' AND user_id=?)
            ORDER BY lt.id DESC LIMIT 30""", (user["id"],)).fetchall()
        reviews = con.execute("SELECT r.*,u.name AS reviewer_name FROM reviews r JOIN users u ON u.id=r.reviewer_id WHERE r.reviewee_id=? ORDER BY r.id DESC LIMIT 20", (user["id"],)).fetchall()
        payment_intents = con.execute("SELECT * FROM payment_intents WHERE advertiser_id=? ORDER BY id DESC LIMIT 20", (user["id"],)).fetchall() if user["role"]=='advertiser' else []
        dispute_rows = con.execute("""SELECT d.*,de.id AS evidence_id,de.evidence_type,de.original_name,de.external_url,de.note AS evidence_note,de.created_at AS evidence_created
            FROM disputes d LEFT JOIN dispute_evidence de ON de.dispute_id=d.id
            WHERE d.placement_id IN (SELECT p.id FROM placements p JOIN request_responses rr ON rr.id=p.response_id
              JOIN advertiser_requests ar ON ar.id=rr.request_id WHERE ar.advertiser_id=? OR rr.supplier_id=?)
            ORDER BY d.id DESC,de.id ASC""", (user['id'],user['id'])).fetchall() if user['role'] in ('advertiser','supplier') else []
        delivery_receipts = con.execute("""SELECT * FROM placement_delivery_receipts WHERE placement_id IN
            (SELECT p.id FROM placements p JOIN request_responses rr ON rr.id=p.response_id
             JOIN advertiser_requests ar ON ar.id=rr.request_id WHERE ar.advertiser_id=? OR rr.supplier_id=?)
            ORDER BY id DESC""",(user['id'],user['id'])).fetchall() if user['role'] in ('advertiser','supplier') else []
    return page(request, "dashboard.html", rows=rows, legacy=legacy, offers=offers,
                available_requests=available_requests, responses=responses, request_responses=request_responses,
                placements=placements, messages=messages, terms=terms, activities=activities, campaigns=campaigns, finance=finance, ledger=ledger, transactions=transactions, reviews=reviews, payment_intents=payment_intents, dispute_rows=dispute_rows, delivery_receipts=delivery_receipts, platform_accounts=platform_accounts if user["role"]=="supplier" else [], workspace_stats=workspace_stats, workspace_actions=workspace_actions, partner_site=partner_site, partner_clicks=partner_clicks, partner_registrations=partner_registrations, partner_7d_clicks=partner_7d_clicks, partner_7d_registrations=partner_7d_registrations, partner_30d_clicks=partner_30d_clicks, partner_30d_registrations=partner_30d_registrations, partner_daily_rows=partner_daily_rows, partner_reward_rows=partner_reward_rows, partner_reward_totals=partner_reward_totals)


@app.get("/media-planner", response_class=HTMLResponse)
def media_planner_page(request: Request):
    user = require_user(request, "advertiser")
    with connect() as con:
        campaigns = con.execute("SELECT id,title,budget,status FROM campaigns WHERE advertiser_id=? AND status!='archived' ORDER BY id DESC", (user["id"],)).fetchall()
        saved_plans = con.execute("SELECT * FROM media_plans WHERE advertiser_id=? ORDER BY id DESC LIMIT 30", (user["id"],)).fetchall()
    return page(request, "media_planner.html", campaigns=campaigns, saved_plans=saved_plans, result=None)

@app.post("/advertiser/media-plans/generate", response_class=HTMLResponse)
def generate_saved_media_plan(request: Request, csrf: str = Form(...), title: str = Form(...), business: str = Form(""),
        goal: str = Form(...), audience: str = Form(""), geo: str = Form("Россия"), budget: int = Form(...), channels: str = Form("")):
    check_csrf(request, csrf); user = require_user(request, "advertiser")
    title=title.strip(); business=business.strip(); goal=goal.strip(); audience=audience.strip(); geo=geo.strip()
    channel_list=[x.strip() for x in channels.split(",") if x.strip()]
    if not 2 <= len(title) <= 140 or len(business)>300 or not goal or len(goal)>1000 or len(audience)>500 or len(geo)>160 or len(channel_list)>12 or any(len(x)>50 for x in channel_list):
        raise HTTPException(400, "Проверь название, цель и ограничения полей медиаплана")
    if budget < 1000 or budget > 100000000: raise HTTPException(400, "Бюджет должен быть от 1 000 до 100 000 000 ₽")
    brief={"business":business,"goal":goal,"audience":audience,"geo":geo or "Россия","budget":budget,"channels":channel_list}
    plan=build_plan(inventory_rows(), goal+' '+business, audience, geo or 'Россия', budget, channel_list)
    strategy=ask_ai("Сформируй короткое объяснение предварительного медиаплана: почему выбраны эти позиции, как распределён бюджет и что проверить перед запуском. Не придумывай показатели, гарантии и цены. Явно укажи ограничения демо-данных.", [], brief, plan['items'])
    with connect() as con:
        cur=con.execute("INSERT INTO media_plans(advertiser_id,title,brief_json,plan_json,strategy) VALUES(?,?,?,?,?)",
            (user['id'],title,json.dumps(brief,ensure_ascii=False),json.dumps(plan,ensure_ascii=False),strategy[:5000]))
        plan_id=cur.lastrowid; con.commit()
    return RedirectResponse(f"/media-planner?created={plan_id}#plan-{plan_id}", status_code=303)

@app.post("/advertiser/media-plans/{plan_id}/attach")
def attach_media_plan(request: Request, plan_id: int, csrf: str = Form(...), campaign_id: int = Form(...)):
    check_csrf(request, csrf); user=require_user(request, "advertiser")
    with connect() as con:
        plan=con.execute("SELECT * FROM media_plans WHERE id=? AND advertiser_id=?", (plan_id,user['id'])).fetchone()
        campaign=con.execute("SELECT * FROM campaigns WHERE id=? AND advertiser_id=?", (campaign_id,user['id'])).fetchone()
        if not plan or not campaign: raise HTTPException(404, "Медиаплан или кампания не найдены")
        if campaign['status'] in ('completed','archived'): raise HTTPException(409, "Нельзя менять медиаплан завершённой или архивной кампании")
        con.execute("UPDATE campaigns SET media_plan_json=?,updated_at=CURRENT_TIMESTAMP WHERE id=?", (plan['plan_json'],campaign_id))
        con.execute("UPDATE media_plans SET campaign_id=?,status='applied',updated_at=CURRENT_TIMESTAMP WHERE id=?", (campaign_id,plan_id))
        con.execute("INSERT INTO campaign_events(campaign_id,actor_id,event_type,details) VALUES(?,?,?,?)", (campaign_id,user['id'],'media_plan_attached',f'К кампании прикреплён медиаплан #{plan_id}: {plan["title"]}'))
        con.commit()
    return RedirectResponse(f"/media-planner?attached={plan_id}#plan-{plan_id}", status_code=303)

@app.get("/advertiser/campaigns/{campaign_id}/analytics", response_class=HTMLResponse)
def campaign_analytics_page(request: Request, campaign_id: int):
    user = require_user(request, "advertiser")
    with connect() as con:
        campaign = con.execute("SELECT * FROM campaigns WHERE id=? AND advertiser_id=?", (campaign_id,user["id"])).fetchone()
        if not campaign: raise HTTPException(404, "Кампания не найдена")
        rows = con.execute("SELECT * FROM campaign_metric_snapshots WHERE campaign_id=? ORDER BY metric_date DESC", (campaign_id,)).fetchall()
    totals = {"spend":sum(r["spend"] for r in rows),"impressions":sum(r["impressions"] for r in rows),
              "clicks":sum(r["clicks"] for r in rows),"conversions":sum(r["conversions"] for r in rows),
              "revenue":sum((r["revenue"] or 0) for r in rows),"revenue_known":bool(rows) and all(r["revenue"] is not None for r in rows)}
    t=totals
    kpis={"ctr":(t["clicks"]/t["impressions"]*100 if t["impressions"] else None),
          "cpc":(t["spend"]/t["clicks"] if t["clicks"] else None),
          "cpm":(t["spend"]/t["impressions"]*1000 if t["impressions"] else None),
          "conversion_rate":(t["conversions"]/t["clicks"]*100 if t["clicks"] else None),
          "cpa":(t["spend"]/t["conversions"] if t["conversions"] else None),
          "roi":(((t["revenue"]-t["spend"])/t["spend"]*100) if t["revenue_known"] and t["spend"] else None)}
    return page(request,"campaign_analytics.html",campaign=campaign,rows=rows,totals=totals,kpis=kpis)

@app.post("/advertiser/campaigns/{campaign_id}/analytics")
def save_campaign_metrics(request: Request, campaign_id: int, csrf: str = Form(...), metric_date: str = Form(...),
        spend: int = Form(...), impressions: int = Form(...), clicks: int = Form(...), conversions: int = Form(...),
        revenue: str = Form(""), note: str = Form("")):
    check_csrf(request,csrf); user=require_user(request,"advertiser")
    try: datetime.strptime(metric_date,"%Y-%m-%d")
    except ValueError: raise HTTPException(400,"Дата должна быть в формате ГГГГ-ММ-ДД")
    vals=(spend,impressions,clicks,conversions)
    if any(v<0 or v>2_000_000_000 for v in vals): raise HTTPException(400,"Показатели должны быть от 0 до 2 млрд")
    rev=None
    if revenue.strip():
        try: rev=int(revenue)
        except ValueError: raise HTTPException(400,"Выручка должна быть целым числом")
        if not 0<=rev<=2_000_000_000: raise HTTPException(400,"Выручка должна быть от 0 до 2 млрд ₽")
    if len(note)>500: raise HTTPException(400,"Комментарий не должен превышать 500 символов")
    if clicks>impressions and impressions>0: raise HTTPException(400,"Клики не могут превышать показы")
    with connect() as con:
        campaign=con.execute("SELECT id FROM campaigns WHERE id=? AND advertiser_id=?",(campaign_id,user["id"])).fetchone()
        if not campaign: raise HTTPException(404,"Кампания не найдена")
        con.execute("""INSERT INTO campaign_metric_snapshots(campaign_id,metric_date,spend,impressions,clicks,conversions,revenue,source,note)
            VALUES(?,?,?,?,?,?,?,'manual',?) ON CONFLICT(campaign_id,metric_date) DO UPDATE SET
            spend=excluded.spend,impressions=excluded.impressions,clicks=excluded.clicks,conversions=excluded.conversions,
            revenue=excluded.revenue,source='manual',note=excluded.note,updated_at=CURRENT_TIMESTAMP""",
            (campaign_id,metric_date,spend,impressions,clicks,conversions,rev,note.strip()))
        con.commit()
    return RedirectResponse(f"/advertiser/campaigns/{campaign_id}/analytics?saved=1",status_code=303)

@app.post("/advertiser/campaigns/{campaign_id}/analytics/import")
async def import_campaign_metrics(request: Request, campaign_id: int, csrf: str = Form(...), file: UploadFile = File(...)):
    """Import advertiser-provided daily CSV snapshots. No third-party API is contacted."""
    check_csrf(request, csrf); user=require_user(request,"advertiser")
    with connect() as con:
        campaign=con.execute("SELECT id FROM campaigns WHERE id=? AND advertiser_id=?",(campaign_id,user["id"])).fetchone()
    if not campaign: raise HTTPException(404,"Кампания не найдена")
    if not file.filename or not file.filename.lower().endswith('.csv'):
        raise HTTPException(400,"Загрузите CSV-файл")
    raw=await file.read(2_000_001)
    if len(raw)>2_000_000: raise HTTPException(413,"CSV не должен превышать 2 МБ")
    try:
        content=raw.decode('utf-8-sig')
        reader=csv.DictReader(io.StringIO(content))
        required={"date","spend","impressions","clicks","conversions"}
        headers={h.strip().lower() for h in (reader.fieldnames or []) if h}
        if not required.issubset(headers):
            raise ValueError("Обязательные колонки: date, spend, impressions, clicks, conversions; revenue и note необязательны")
        # Normalize header whitespace/case without silently mapping unknown columns.
        normalized=[]
        seen_dates=set()
        for line,row in enumerate(reader,start=2):
            if len(normalized)>=366: raise ValueError("За один импорт можно загрузить не более 366 строк")
            item={str(k or '').strip().lower(): (v or '').strip() for k,v in row.items()}
            if not any(item.values()): continue
            try:
                day=item['date']; datetime.strptime(day,'%Y-%m-%d')
                vals={k:int(item[k]) for k in ('spend','impressions','clicks','conversions')}
                rev=int(item['revenue']) if item.get('revenue','') else None
            except (ValueError,KeyError):
                raise ValueError(f"Строка {line}: неверная дата или целочисленное значение")
            if day in seen_dates: raise ValueError(f"Строка {line}: дата {day} повторяется в файле")
            seen_dates.add(day)
            if any(v<0 or v>2_000_000_000 for v in vals.values()) or (rev is not None and not 0<=rev<=2_000_000_000):
                raise ValueError(f"Строка {line}: значения должны быть от 0 до 2 млрд")
            if vals['impressions'] and vals['clicks']>vals['impressions']:
                raise ValueError(f"Строка {line}: клики не могут превышать показы")
            note=item.get('note','')
            if len(note)>500: raise ValueError(f"Строка {line}: комментарий длиннее 500 символов")
            normalized.append((day,vals['spend'],vals['impressions'],vals['clicks'],vals['conversions'],rev,note))
        if not normalized: raise ValueError("В CSV нет строк с данными")
    except UnicodeDecodeError:
        raise HTTPException(400,"Файл должен быть в кодировке UTF-8")
    except (ValueError,csv.Error) as exc:
        raise HTTPException(400,str(exc))
    with connect() as con:
        for day,spend,impressions,clicks,conversions,rev,note in normalized:
            con.execute("""INSERT INTO campaign_metric_snapshots(campaign_id,metric_date,spend,impressions,clicks,conversions,revenue,source,note)
                VALUES(?,?,?,?,?,?,?,'import',?) ON CONFLICT(campaign_id,metric_date) DO UPDATE SET
                spend=excluded.spend,impressions=excluded.impressions,clicks=excluded.clicks,conversions=excluded.conversions,
                revenue=excluded.revenue,source='import',note=excluded.note,updated_at=CURRENT_TIMESTAMP""",
                (campaign_id,day,spend,impressions,clicks,conversions,rev,note))
        con.commit()
    return RedirectResponse(f"/advertiser/campaigns/{campaign_id}/analytics?imported={len(normalized)}",status_code=303)

@app.get("/advertiser/campaigns/{campaign_id}/analytics.csv")
def export_campaign_metrics(request: Request, campaign_id: int):
    user=require_user(request,"advertiser")
    with connect() as con:
        campaign=con.execute("SELECT id,title FROM campaigns WHERE id=? AND advertiser_id=?",(campaign_id,user["id"])).fetchone()
        if not campaign: raise HTTPException(404,"Кампания не найдена")
        rows=con.execute("SELECT metric_date,spend,impressions,clicks,conversions,revenue,source,note FROM campaign_metric_snapshots WHERE campaign_id=? ORDER BY metric_date",(campaign_id,)).fetchall()
    output=io.StringIO(); writer=csv.writer(output); writer.writerow(["date","spend_rub","impressions","clicks","conversions","revenue_rub","source","note"])
    for row in rows: writer.writerow(list(row))
    safe=re.sub(r"[^a-zA-Z0-9_-]+","_",campaign["title"]).strip("_")[:40] or "campaign"
    return StreamingResponse(iter(["\ufeff"+output.getvalue()]),media_type="text/csv; charset=utf-8",headers={"Content-Disposition":f'attachment; filename="campaign_{campaign_id}_{safe}_metrics.csv"'})

@app.post("/advertiser/campaigns")
def create_campaign(request: Request, csrf: str = Form(...), title: str = Form(...), objective: str = Form(""),
                   budget: int = Form(...), geo: str = Form(""), channels: str = Form(""), audience: str = Form(""),
                   request_id: int = Form(0), start_date: str = Form(""), end_date: str = Form("")):
    check_csrf(request, csrf); user = require_user(request, "advertiser")
    title = title.strip(); objective = objective.strip(); geo = geo.strip(); channels = channels.strip(); audience = audience.strip()
    if not 2 <= len(title) <= 140 or len(objective) > 1500 or len(geo) > 160 or len(channels) > 300 or len(audience) > 300:
        raise HTTPException(400, "Проверь название и ограничения полей кампании")
    if budget < 1000 or budget > 100000000:
        raise HTTPException(400, "Бюджет кампании должен быть от 1 000 до 100 000 000 ₽")
    if start_date and end_date and end_date < start_date:
        raise HTTPException(400, "Дата окончания не может быть раньше даты начала")
    with connect() as con:
        linked_request = None
        if request_id:
            linked_request = con.execute("SELECT id FROM advertiser_requests WHERE id=? AND advertiser_id=?", (request_id,user["id"])).fetchone()
            if not linked_request: raise HTTPException(404, "Своя заявка не найдена")
        cur = con.execute("""INSERT INTO campaigns(advertiser_id,request_id,title,objective,budget,geo,channels,audience,start_date,end_date)
            VALUES(?,?,?,?,?,?,?,?,?,?)""", (user["id"],request_id or None,title,objective,budget,geo,channels,audience,start_date,end_date))
        cid = cur.lastrowid
        con.execute("INSERT INTO campaign_events(campaign_id,actor_id,event_type,details) VALUES(?,?,?,?)", (cid,user["id"],"created","Кампания создана как черновик"))
        con.commit()
    return RedirectResponse(f"/dashboard?campaign={cid}#campaigns", status_code=303)

@app.post("/advertiser/campaigns/{campaign_id}/status")
def update_campaign_status(request: Request, campaign_id: int, csrf: str = Form(...), status: str = Form(...)):
    check_csrf(request, csrf); user = require_user(request, "advertiser")
    allowed = {"draft", "active", "paused", "completed", "archived"}
    labels = {"draft":"Черновик", "active":"Активна", "paused":"Приостановлена", "completed":"Завершена", "archived":"В архиве"}
    if status not in allowed: raise HTTPException(400, "Недопустимый статус кампании")
    with connect() as con:
        row = con.execute("SELECT id,status FROM campaigns WHERE id=? AND advertiser_id=?", (campaign_id,user["id"])).fetchone()
        if not row: raise HTTPException(404, "Кампания не найдена")
        old = row["status"]
        if old == "archived" and status != "archived": raise HTTPException(409, "Архивную кампанию нельзя возобновить")
        if old == "completed" and status in ("active","paused"): raise HTTPException(409, "Завершённую кампанию нельзя повторно активировать")
        con.execute("UPDATE campaigns SET status=?,updated_at=CURRENT_TIMESTAMP WHERE id=?", (status,campaign_id))
        con.execute("INSERT INTO campaign_events(campaign_id,actor_id,event_type,details) VALUES(?,?,?,?)", (campaign_id,user["id"],"status_changed",f"{labels[old]} → {labels[status]}"))
        con.commit()
    return RedirectResponse(f"/dashboard#campaign-{campaign_id}", status_code=303)

@app.post("/profile")
def update_profile(request: Request, csrf: str = Form(...), name: str = Form(...), company: str = Form("")):
    check_csrf(request, csrf); user = require_user(request)
    if not name.strip() or len(name.strip()) > 100 or len(company) > 160: raise HTTPException(400, "Проверь поля профиля")
    with connect() as con:
        con.execute("UPDATE users SET name=?,company=? WHERE id=?", (name.strip(), company.strip(), user["id"]))
        con.commit()
    return RedirectResponse("/dashboard?profile=updated", status_code=303)

@app.post("/supplier/offers")
def supplier_offer(request: Request, csrf: str = Form(...), name: str = Form(...), channel: str = Form(...),
                   topic: str = Form(...), geo: str = Form(...), price: int = Form(...), audience: int = Form(0),
                   formats: str = Form(...), details: str = Form("")):
    check_csrf(request, csrf); user = require_user(request, "supplier")
    with connect() as con:
        promotion = con.execute("SELECT status FROM partner_promotion_sites WHERE supplier_id=?", (user['id'],)).fetchone()
    if not promotion or promotion['status'] != 'approved':
        raise HTTPException(403, "Сначала размести рекламу ADMARKET и дождись подтверждения администратора в разделе «Partner Promotion».")
    if not name.strip() or not channel.strip() or not topic.strip() or not geo.strip() or not formats.strip():
        raise HTTPException(400, "Заполни обязательные поля")
    if price < 1 or price > 100000000 or audience < 0 or audience > 1000000000: raise HTTPException(400, "Проверь цену и размер аудитории")
    with connect() as con:
        cur = con.execute("""INSERT INTO supplier_offers(supplier_id,name,channel,topic,geo,price,audience,formats,details)
            VALUES(?,?,?,?,?,?,?,?,?)""", (user["id"],name.strip(),channel.strip(),topic.strip(),geo.strip(),price,audience,formats.strip(),details.strip()))
        offer_id = cur.lastrowid
        con.commit()
    moderate_text("supplier_offer", offer_id, {"name":name,"channel":channel,"topic":topic,"geo":geo,"formats":formats,"details":details,"user_id":user["id"]})
    return RedirectResponse("/dashboard?offer=submitted", status_code=303)

@app.post("/advertiser/requests")
def advertiser_request(request: Request, csrf: str = Form(...), title: str = Form(...), goal: str = Form(...),
                       geo: str = Form(...), budget: int = Form(...), audience: str = Form(""), channels: str = Form("")):
    check_csrf(request, csrf); user = require_user(request, "advertiser")
    if not title.strip() or not goal.strip() or not geo.strip() or budget < 1000 or budget > 100000000:
        raise HTTPException(400, "Проверь бриф и бюджет (от 1 000 ₽)")
    with connect() as con:
        cur = con.execute("""INSERT INTO advertiser_requests(advertiser_id,title,goal,geo,budget,audience,channels,status)
            VALUES(?,?,?,?,?,?,?,'Открыта')""", (user["id"],title.strip(),goal.strip(),geo.strip(),budget,audience.strip(),channels.strip()))
        request_id = cur.lastrowid
        con.commit()
    moderate_text("advertiser_request", request_id, {"title":title,"goal":goal,"geo":geo,"audience":audience,"channels":channels,"user_id":user["id"]})
    return RedirectResponse("/dashboard?request=submitted", status_code=303)

@app.post("/supplier/requests/{request_id}/respond")
def supplier_respond(request: Request, request_id: int, csrf: str = Form(...), proposed_price: int = Form(...),
                     message: str = Form(""), supplier_offer_id: int = Form(0)):
    check_csrf(request, csrf); user = require_user(request, "supplier")
    if proposed_price < 1 or proposed_price > 100000000 or len(message.strip()) > 2000:
        raise HTTPException(400, "Проверь цену и сообщение")
    with connect() as con:
        req = con.execute("SELECT id,status FROM advertiser_requests WHERE id=?", (request_id,)).fetchone()
        if not req or req["status"] != "Открыта": raise HTTPException(404, "Открытая заявка не найдена")
        offer_id = None
        if supplier_offer_id:
            offer = con.execute("SELECT id FROM supplier_offers WHERE id=? AND supplier_id=? AND status='approved'",
                                (supplier_offer_id, user["id"])).fetchone()
            if not offer: raise HTTPException(400, "Можно прикрепить только своё одобренное предложение")
            offer_id = offer["id"]
        try:
            con.execute("""INSERT INTO request_responses(request_id,supplier_id,supplier_offer_id,proposed_price,message)
                VALUES(?,?,?,?,?)""", (request_id,user["id"],offer_id,proposed_price,message.strip()))
            con.commit()
        except Exception as exc:
            if "UNIQUE" in str(exc).upper(): raise HTTPException(409, "Ты уже ответил на эту заявку")
            raise
    return RedirectResponse("/dashboard?response=submitted", status_code=303)

@app.post("/advertiser/responses/{response_id}/decision")
def advertiser_decision(request: Request, response_id: int, csrf: str = Form(...), decision: str = Form(...)):
    check_csrf(request, csrf); user = require_user(request, "advertiser")
    if decision not in ("accepted", "rejected"): raise HTTPException(400, "Недопустимое решение")
    with connect() as con:
        row = con.execute("""SELECT rr.id,rr.request_id,rr.status,ar.status AS request_status
            FROM request_responses rr JOIN advertiser_requests ar ON ar.id=rr.request_id
            WHERE rr.id=? AND ar.advertiser_id=?""", (response_id,user["id"])).fetchone()
        if not row: raise HTTPException(404, "Отклик не найден")
        if row["request_status"] != "Открыта" or row["status"] != "pending":
            raise HTTPException(409, "Это предложение уже нельзя изменить")
        con.execute("UPDATE request_responses SET status=? WHERE id=?", (decision,response_id))
        if decision == "accepted":
            con.execute("UPDATE advertiser_requests SET status='Поставщик выбран' WHERE id=?", (row["request_id"],))
            con.execute("INSERT OR IGNORE INTO placements(response_id) VALUES(?)", (response_id,))
            placement_id = con.execute("SELECT id FROM placements WHERE response_id=?", (response_id,)).fetchone()["id"]
            supplier_id = con.execute("SELECT supplier_id FROM request_responses WHERE id=?", (response_id,)).fetchone()["supplier_id"]
            notify(con, supplier_id, "supplier_selected", "Ваше предложение выбрано", "Рекламодатель выбрал ваше предложение. Обсудите и согласуйте условия размещения.", "/dashboard#placements", placement_id, user["id"])
            con.execute("UPDATE request_responses SET status='rejected' WHERE request_id=? AND id!=? AND status='pending'",
                        (row["request_id"],response_id))
        con.commit()
    return RedirectResponse("/dashboard?decision=saved", status_code=303)

@app.post("/marketplace/placements/{placement_id}/messages")
def placement_message(request: Request, placement_id: int, csrf: str = Form(...), body: str = Form(...)):
    check_csrf(request, csrf); user = require_user(request)
    body = body.strip()
    if not body or len(body) > 3000:
        raise HTTPException(400, "Сообщение должно содержать от 1 до 3000 символов")
    with connect() as con:
        placement = con.execute("""SELECT p.id, rr.id AS response_id, rr.supplier_id, ar.advertiser_id, rr.status AS response_status
            FROM placements p JOIN request_responses rr ON rr.id=p.response_id
            JOIN advertiser_requests ar ON ar.id=rr.request_id WHERE p.id=?""", (placement_id,)).fetchone()
        if not placement: raise HTTPException(404, "Переписка не найдена")
        if user["role"] == "advertiser": allowed = user["id"] == placement["advertiser_id"]
        elif user["role"] == "supplier": allowed = user["id"] == placement["supplier_id"]
        else: allowed = False
        if not allowed or placement["response_status"] != "accepted":
            raise HTTPException(403, "Нет доступа к этой переписке")
        con.execute("INSERT INTO messages(response_id,sender_id,body) VALUES(?,?,?)",
                    (placement["response_id"], user["id"], body))
        recipient_id = placement["supplier_id"] if user["role"] == "advertiser" else placement["advertiser_id"]
        notify(con, recipient_id, "new_message", "Новое сообщение по размещению", body[:180], "/dashboard#placements", placement_id, user["id"])
        con.commit()
    return RedirectResponse("/dashboard#placements", status_code=303)

@app.post("/supplier/placements/{placement_id}/confirm")
def supplier_confirm_placement(request: Request, placement_id: int, csrf: str = Form(...), note: str = Form("")):
    check_csrf(request, csrf); user = require_user(request, "supplier")
    if len(note.strip()) > 1000: raise HTTPException(400, "Комментарий слишком длинный")
    with connect() as con:
        cur = con.execute("""UPDATE placements SET status='confirmed', supplier_note=?, updated_at=CURRENT_TIMESTAMP
            WHERE id=? AND status='awaiting_supplier' AND response_id IN
            (SELECT id FROM request_responses WHERE supplier_id=? AND status='accepted')""",
            (note.strip(), placement_id, user["id"]))
        if cur.rowcount == 0: raise HTTPException(404, "Этап не найден или уже обработан")
        parties = con.execute("""SELECT ar.advertiser_id FROM placements p JOIN request_responses rr ON rr.id=p.response_id
            JOIN advertiser_requests ar ON ar.id=rr.request_id WHERE p.id=?""", (placement_id,)).fetchone()
        con.execute("INSERT OR IGNORE INTO supplier_sla_cases(placement_id,supplier_id,due_at) VALUES(?,?,datetime('now','+72 hours'))", (placement_id,user["id"]))
        notify(con, parties["advertiser_id"], "placement_confirmed", "Поставщик подтвердил размещение", note.strip() or "Поставщик подтвердил возможность размещения.", "/dashboard#placements", placement_id, user["id"])
        con.commit()
    return RedirectResponse("/dashboard#placements", status_code=303)

@app.post("/supplier/placements/{placement_id}/published")
def supplier_mark_published(request: Request, placement_id: int, csrf: str = Form(...), publication_url: str = Form("")):
    check_csrf(request, csrf); user = require_user(request, "supplier")
    publication_url = publication_url.strip()
    if len(publication_url) > 1000 or not re.match(r"^https://", publication_url, re.I):
        raise HTTPException(400, "Для подтверждения публикации нужна действующая HTTPS-ссылка")
    with connect() as con:
        con.execute("BEGIN IMMEDIATE")
        cur = con.execute("""UPDATE placements SET status='published', publication_url=?, updated_at=CURRENT_TIMESTAMP
            WHERE id=? AND status='confirmed' AND response_id IN
            (SELECT id FROM request_responses WHERE supplier_id=? AND status='accepted')""",
            (publication_url, placement_id, user["id"]))
        if cur.rowcount == 0: raise HTTPException(404, "Подтверждённое размещение не найдено")
        con.execute("INSERT INTO placement_delivery_receipts(placement_id,supplier_id,publication_url) VALUES(?,?,?)",
                    (placement_id,user["id"],publication_url))
        con.execute("UPDATE supplier_sla_cases SET status='met',fulfilled_at=CURRENT_TIMESTAMP WHERE placement_id=? AND status='active'",(placement_id,))
        parties = con.execute("""SELECT ar.advertiser_id FROM placements p JOIN request_responses rr ON rr.id=p.response_id
            JOIN advertiser_requests ar ON ar.id=rr.request_id WHERE p.id=?""", (placement_id,)).fetchone()
        notify(con, parties["advertiser_id"], "placement_published", "Поставщик отправил подтверждение публикации", publication_url, "/dashboard#placements", placement_id, user["id"])
        con.execute("INSERT INTO activity_log(actor_id,placement_id,event_type,summary) VALUES(?,?,?,?)",
                    (user["id"],placement_id,"publication_proof_submitted","Поставщик отправил HTTPS-ссылку на опубликованное размещение"))
        con.commit()
    return RedirectResponse("/dashboard#placements", status_code=303)

@app.post("/advertiser/placements/{placement_id}/complete")
def advertiser_complete_placement(request: Request, placement_id: int, csrf: str = Form(...)):
    check_csrf(request, csrf); user = require_user(request, "advertiser")
    with connect() as con:
        con.execute("BEGIN IMMEDIATE")
        proof = con.execute("SELECT * FROM placement_delivery_receipts WHERE placement_id=?",(placement_id,)).fetchone()
        if not proof: raise HTTPException(409, "Нельзя подтвердить выполнение без сохранённого подтверждения публикации")
        cur = con.execute("""UPDATE placements SET status='completed', updated_at=CURRENT_TIMESTAMP
            WHERE id=? AND status='published' AND response_id IN
            (SELECT rr.id FROM request_responses rr JOIN advertiser_requests ar ON ar.id=rr.request_id
             WHERE ar.advertiser_id=? AND rr.status='accepted')""", (placement_id, user["id"]))
        if cur.rowcount == 0: raise HTTPException(404, "Опубликованное размещение не найдено")
        con.execute("UPDATE placement_delivery_receipts SET status='accepted',accepted_by=?,accepted_at=CURRENT_TIMESTAMP WHERE placement_id=? AND status='submitted'",(user["id"],placement_id))
        con.execute("INSERT INTO activity_log(actor_id,placement_id,event_type,summary) VALUES(?,?,?,?)",
                    (user["id"],placement_id,"publication_proof_accepted","Рекламодатель подтвердил выполнение по сохранённой ссылке"))
        parties = con.execute("""SELECT rr.supplier_id FROM placements p JOIN request_responses rr ON rr.id=p.response_id
            WHERE p.id=?""", (placement_id,)).fetchone()
        notify(con, parties["supplier_id"], "placement_completed", "Рекламодатель подтвердил выполнение", "Публикация подтверждена по сохранённой ссылке.", "/dashboard#placements", placement_id, user["id"])
        con.commit()
    return RedirectResponse("/dashboard#placements", status_code=303)

@app.post("/supplier/placements/{placement_id}/terms")
def supplier_propose_terms(request: Request, placement_id: int, csrf: str = Form(...), schedule: str = Form(""),
                           deliverables: str = Form(...), price: int = Form(...), payment_note: str = Form(""),
                           cancellation_note: str = Form(""), extra_terms: str = Form("")):
    check_csrf(request, csrf); user = require_user(request, "supplier")
    fields = [schedule.strip(), deliverables.strip(), payment_note.strip(), cancellation_note.strip(), extra_terms.strip()]
    if not fields[1] or len(fields[0]) > 300 or len(fields[1]) > 2000 or any(len(x) > 1500 for x in fields[2:]):
        raise HTTPException(400, "Проверь содержание и длину условий")
    if price < 1 or price > 100000000:
        raise HTTPException(400, "Цена должна быть от 1 до 100 000 000 ₽")
    with connect() as con:
        row = con.execute("""SELECT p.id,rr.id AS response_id,ar.advertiser_id,rr.proposed_price
            FROM placements p JOIN request_responses rr ON rr.id=p.response_id
            JOIN advertiser_requests ar ON ar.id=rr.request_id
            WHERE p.id=? AND rr.supplier_id=? AND rr.status='accepted'""", (placement_id,user["id"])).fetchone()
        if not row: raise HTTPException(404, "Размещение не найдено")
        latest = con.execute("SELECT version FROM placement_terms WHERE placement_id=? ORDER BY version DESC LIMIT 1", (placement_id,)).fetchone()
        version = (latest["version"] if latest else 0) + 1
        con.execute("UPDATE placement_terms SET status='superseded' WHERE placement_id=? AND status='proposed'", (placement_id,))
        con.execute("""INSERT INTO placement_terms(placement_id,version,schedule,deliverables,price,payment_note,cancellation_note,extra_terms,proposed_by)
            VALUES(?,?,?,?,?,?,?,?,?)""", (placement_id,version,fields[0],fields[1],price,fields[2],fields[3],fields[4],user["id"]))
        notify(con,row["advertiser_id"],"terms_proposed","Поставщик предложил условия",f"Версия {version}; цена {price} ₽. Проверьте условия в кабинете.","/dashboard#placements",placement_id,user["id"])
        con.commit()
    return RedirectResponse("/dashboard#placements", status_code=303)

@app.post("/advertiser/placements/{placement_id}/terms/{terms_id}/decision")
def advertiser_terms_decision(request: Request, placement_id: int, terms_id: int, csrf: str = Form(...), decision: str = Form(...)):
    check_csrf(request, csrf); user = require_user(request, "advertiser")
    if decision not in ("accepted", "rejected"): raise HTTPException(400, "Недопустимое решение")
    with connect() as con:
        row = con.execute("""SELECT pt.*,ar.advertiser_id,rr.supplier_id FROM placement_terms pt
            JOIN placements p ON p.id=pt.placement_id JOIN request_responses rr ON rr.id=p.response_id
            JOIN advertiser_requests ar ON ar.id=rr.request_id
            WHERE pt.id=? AND pt.placement_id=? AND ar.advertiser_id=?""", (terms_id,placement_id,user["id"])).fetchone()
        if not row: raise HTTPException(404, "Версия условий не найдена")
        if row["status"] != "proposed": raise HTTPException(409, "Эта версия уже обработана")
        if decision == "accepted":
            con.execute("UPDATE placement_terms SET status='accepted',advertiser_accepted_at=CURRENT_TIMESTAMP WHERE id=?", (terms_id,))
            rate = commission_rate(con)
            commission = int(round(row["price"] * rate / 100))
            supplier_net = max(0, row["price"] - commission)
            # Snapshot is an estimate only: quoted amount is split into platform fee and supplier net.
            con.execute("""INSERT OR IGNORE INTO financial_estimates
                (placement_id,terms_id,advertiser_id,supplier_id,base_amount,commission_rate,commission_amount,
                 supplier_estimated_net,advertiser_estimated_total)
                VALUES(?,?,?,?,?,?,?,?,?)""",
                (placement_id,terms_id,user["id"],row["supplier_id"],row["price"],rate,
                 commission,supplier_net,row["price"]))
            title = "Условия размещения согласованы"
            body = f"Рекламодатель принял версию {row['version']} условий. Это запись согласования в MVP, не юридически оформленный договор."
            kind = "terms_accepted"
        else:
            con.execute("UPDATE placement_terms SET status='rejected' WHERE id=?", (terms_id,))
            title = "Условия размещения отклонены"
            body = f"Рекламодатель отклонил версию {row['version']}. Обсудите изменения в переписке."
            kind = "terms_rejected"
        notify(con,row["supplier_id"],kind,title,body,"/dashboard#placements",placement_id,user["id"])
        con.commit()
    return RedirectResponse("/dashboard#placements", status_code=303)

@app.post("/notifications/{notification_id}/read")
def notification_read(request: Request, notification_id: int, csrf: str = Form(...)):
    check_csrf(request, csrf); user = require_user(request)
    with connect() as con:
        con.execute("UPDATE notifications SET is_read=1 WHERE id=? AND user_id=?", (notification_id,user["id"]))
        con.commit()
    return RedirectResponse("/dashboard", status_code=303)

@app.post("/notifications/read-all")
def notifications_read_all(request: Request, csrf: str = Form(...)):
    check_csrf(request, csrf); user = require_user(request)
    with connect() as con:
        con.execute("UPDATE notifications SET is_read=1 WHERE user_id=?", (user["id"],)); con.commit()
    return RedirectResponse("/dashboard", status_code=303)

@app.get("/admin", response_class=HTMLResponse)
def admin(request: Request):
    user = current_user(request)
    if not user: return RedirectResponse("/login", status_code=303)
    if not is_authorized_admin(user): raise HTTPException(403, "Доступ к панели администратора запрещён.")
    with connect() as con:
        configured_commission = commission_rate(con)
        users = con.execute("""SELECT u.id,u.email,u.role,u.name,u.company,u.created_at,u.is_active,u.email_verified,u.supplier_verification,u.risk_level,
            (SELECT ROUND(AVG(r.rating),2) FROM reviews r WHERE r.reviewee_id=u.id) AS avg_rating,
            (SELECT COUNT(*) FROM reviews r WHERE r.reviewee_id=u.id) AS review_count,
            (SELECT COUNT(*) FROM supplier_complaints c WHERE c.supplier_id=u.id AND c.status IN ('open','in_review')) AS open_complaints
            FROM users u ORDER BY u.id DESC""").fetchall()
        orders = con.execute("SELECT * FROM orders ORDER BY id DESC").fetchall()
        requests = con.execute("SELECT ar.*,u.email FROM advertiser_requests ar JOIN users u ON u.id=ar.advertiser_id ORDER BY ar.id DESC").fetchall()
        offers = con.execute("SELECT so.*,u.email FROM supplier_offers so JOIN users u ON u.id=so.supplier_id ORDER BY so.id DESC").fetchall()
        partner_reward_amount_value = partner_reward_amount(con)
        partner_reward_rows = con.execute("""SELECT pr.*,su.email AS supplier_email,au.email AS advertiser_email,
            p.id AS placement_id FROM partner_rewards pr JOIN users su ON su.id=pr.supplier_id
            JOIN users au ON au.id=pr.advertiser_id JOIN placements p ON p.id=pr.placement_id
            ORDER BY CASE pr.status WHEN 'pending' THEN 0 WHEN 'approved' THEN 1 ELSE 2 END,pr.id DESC LIMIT 500""").fetchall()
        partner_reward_flag_rows = con.execute("""SELECT f.*,su.email AS supplier_email,au.email AS advertiser_email
            FROM partner_reward_flags f LEFT JOIN users su ON su.id=f.supplier_id
            LEFT JOIN users au ON au.id=f.advertiser_id
            ORDER BY CASE f.status WHEN 'open' THEN 0 ELSE 1 END,f.id DESC LIMIT 300""").fetchall()
        partner_promotion_rows = con.execute("""SELECT pps.*,u.email,u.name,u.company,u.referral_code,
            (SELECT COUNT(*) FROM partner_referral_events e WHERE e.supplier_id=pps.supplier_id AND e.event_type='click') AS referral_clicks,
            (SELECT COUNT(*) FROM partner_referral_events e WHERE e.supplier_id=pps.supplier_id AND e.event_type='registration') AS referred_advertisers,
            ROUND(100.0 * (SELECT COUNT(*) FROM partner_referral_events e WHERE e.supplier_id=pps.supplier_id AND e.event_type='registration') / NULLIF((SELECT COUNT(*) FROM partner_referral_events e WHERE e.supplier_id=pps.supplier_id AND e.event_type='click'),0),1) AS referral_conversion
            FROM partner_promotion_sites pps JOIN users u ON u.id=pps.supplier_id
            ORDER BY CASE pps.status WHEN 'pending' THEN 0 WHEN 'approved' THEN 1 ELSE 2 END,pps.id DESC""").fetchall()
        finance_summary = con.execute("""SELECT COUNT(*) AS records,COALESCE(SUM(base_amount),0) AS base_total,
            COALESCE(SUM(commission_amount),0) AS commission_total,
            COALESCE(SUM(supplier_estimated_net),0) AS supplier_net_total,
            COALESCE(SUM(advertiser_estimated_total),0) AS advertiser_total
            FROM financial_estimates""").fetchone()
        payment_rows = con.execute("SELECT * FROM payment_intents ORDER BY id DESC LIMIT 200").fetchall()
        dispute_rows = con.execute("""SELECT d.*,p.status AS placement_status,ar.title AS request_title,
            au.email AS advertiser_email,su.email AS supplier_email,op.email AS opened_by_email,
            (SELECT COUNT(*) FROM dispute_evidence de WHERE de.dispute_id=d.id) AS evidence_count
            FROM disputes d JOIN placements p ON p.id=d.placement_id
            JOIN request_responses rr ON rr.id=p.response_id JOIN advertiser_requests ar ON ar.id=rr.request_id
            JOIN users au ON au.id=ar.advertiser_id JOIN users su ON su.id=rr.supplier_id
            JOIN users op ON op.id=d.opened_by ORDER BY CASE d.status WHEN 'open' THEN 0 WHEN 'in_review' THEN 1 ELSE 2 END,d.id DESC LIMIT 200""").fetchall()
        admin_evidence_rows = con.execute("""SELECT de.*,d.id AS dispute_id FROM dispute_evidence de
            JOIN disputes d ON d.id=de.dispute_id ORDER BY de.id DESC LIMIT 500""").fetchall()
        moderation_rows = con.execute("""SELECT mr.*, CASE WHEN mr.entity_type='supplier_offer' THEN so.name ELSE ar.title END AS entity_title,
            CASE WHEN mr.entity_type='supplier_offer' THEN u.email ELSE au.email END AS owner_email
            FROM moderation_records mr
            LEFT JOIN supplier_offers so ON mr.entity_type='supplier_offer' AND so.id=mr.entity_id
            LEFT JOIN users u ON u.id=so.supplier_id
            LEFT JOIN advertiser_requests ar ON mr.entity_type='advertiser_request' AND ar.id=mr.entity_id
            LEFT JOIN users au ON au.id=ar.advertiser_id
            ORDER BY CASE mr.decision WHEN 'review' THEN 0 ELSE 1 END, mr.id DESC LIMIT 200""").fetchall()
        escrow_rows = con.execute("SELECT * FROM escrow_events ORDER BY id DESC LIMIT 200").fetchall()
        risk_rows = con.execute("""SELECT re.*,u.email FROM risk_events re LEFT JOIN users u ON u.id=re.user_id ORDER BY re.id DESC LIMIT 200""").fetchall()
        complaint_rows = con.execute("""SELECT c.*,ar.title AS request_title,op.email AS opener_email,su.email AS supplier_email
            FROM supplier_complaints c JOIN placements p ON p.id=c.placement_id JOIN request_responses rr ON rr.id=p.response_id
            JOIN advertiser_requests ar ON ar.id=rr.request_id JOIN users op ON op.id=c.opened_by JOIN users su ON su.id=c.supplier_id
            ORDER BY CASE c.status WHEN 'open' THEN 0 WHEN 'in_review' THEN 1 ELSE 2 END,c.id DESC LIMIT 300""").fetchall()
        sla_rows = con.execute("""SELECT sc.*,u.email,p.status AS placement_status,ar.title AS request_title
            FROM supplier_sla_cases sc JOIN users u ON u.id=sc.supplier_id JOIN placements p ON p.id=sc.placement_id
            JOIN request_responses rr ON rr.id=p.response_id JOIN advertiser_requests ar ON ar.id=rr.request_id
            ORDER BY CASE sc.status WHEN 'breached' THEN 0 WHEN 'active' THEN 1 ELSE 2 END,sc.id DESC LIMIT 300""").fetchall()
        quality_rows = []
        for supplier_row in con.execute("SELECT id,email FROM users WHERE role='supplier' ORDER BY id DESC").fetchall():
            q = supplier_quality(con, supplier_row['id'])
            if q:
                q['email'] = supplier_row['email']; quality_rows.append(q)
        fraud_rows = con.execute("""SELECT f.*,u.email,so.name AS offer_name FROM fraud_review_signals f
            LEFT JOIN users u ON u.id=f.supplier_id LEFT JOIN supplier_offers so ON so.id=f.offer_id
            ORDER BY CASE f.status WHEN 'open' THEN 0 ELSE 1 END, f.id DESC LIMIT 300""").fetchall()
        platform_account_rows = con.execute("""SELECT pa.*,u.email,
            (SELECT COUNT(*) FROM platform_metric_snapshots ms WHERE ms.account_id=pa.id) AS snapshot_count,
            (SELECT trust_score FROM platform_trust_assessments ta WHERE ta.account_id=pa.id ORDER BY ta.id DESC LIMIT 1) AS trust_score,
            (SELECT confidence FROM platform_trust_assessments ta WHERE ta.account_id=pa.id ORDER BY ta.id DESC LIMIT 1) AS confidence
            FROM platform_accounts pa JOIN users u ON u.id=pa.supplier_id ORDER BY pa.id DESC LIMIT 300""").fetchall()
        platform_signal_rows = con.execute("""SELECT pts.*,pa.platform,pa.handle,pa.profile_url,u.email FROM platform_trust_signals pts
            JOIN platform_accounts pa ON pa.id=pts.account_id JOIN users u ON u.id=pa.supplier_id
            ORDER BY CASE pts.status WHEN 'open' THEN 0 ELSE 1 END,pts.id DESC LIMIT 300""").fetchall()
        finance_rows = con.execute("""SELECT fe.*,pt.version,ar.title AS request_title,
            au.email AS advertiser_email,su.email AS supplier_email
            FROM financial_estimates fe JOIN placement_terms pt ON pt.id=fe.terms_id
            JOIN placements p ON p.id=fe.placement_id JOIN request_responses rr ON rr.id=p.response_id
            JOIN advertiser_requests ar ON ar.id=rr.request_id
            JOIN users au ON au.id=fe.advertiser_id JOIN users su ON su.id=fe.supplier_id
            ORDER BY fe.id DESC LIMIT 500""").fetchall()
    return page(request, "admin.html", users=users, orders=orders, requests=requests, offers=offers,
                finance_summary=finance_summary, finance_rows=finance_rows, commission_rate=configured_commission,
                payment_rows=payment_rows, risk_rows=risk_rows, moderation_rows=moderation_rows, escrow_rows=escrow_rows, dispute_rows=dispute_rows, admin_evidence_rows=admin_evidence_rows, complaint_rows=complaint_rows, sla_rows=sla_rows, quality_rows=quality_rows, fraud_rows=fraud_rows, platform_account_rows=platform_account_rows, platform_signal_rows=platform_signal_rows, partner_promotion_rows=partner_promotion_rows, partner_reward_amount=partner_reward_amount_value, partner_reward_rows=partner_reward_rows, partner_reward_flag_rows=partner_reward_flag_rows)

@app.get("/supplier/partner-promotion/export.csv")
def supplier_partner_promotion_export(request: Request, period: str = "30"):
    """Export only the authenticated supplier's aggregated referral events."""
    user = require_user(request, "supplier")
    if period not in {"7", "30", "90", "365"}:
        raise HTTPException(400, "Недопустимый период отчёта")
    with connect() as con:
        rows = con.execute("""SELECT date(created_at) AS day,
            SUM(CASE WHEN event_type='click' THEN 1 ELSE 0 END) AS clicks,
            SUM(CASE WHEN event_type='registration' THEN 1 ELSE 0 END) AS registrations
            FROM partner_referral_events WHERE supplier_id=? AND created_at >= datetime('now', ?)
            GROUP BY date(created_at) ORDER BY day""", (user["id"], f"-{int(period)} days")).fetchall()
    output = io.StringIO(newline="")
    writer = csv.writer(output, delimiter=";")
    writer.writerow(["date_utc", "referral_clicks", "advertiser_registrations", "conversion_percent"])
    for row in rows:
        clicks = int(row["clicks"] or 0); registrations = int(row["registrations"] or 0)
        conversion = round(registrations * 100 / clicks, 2) if clicks else 0
        writer.writerow([row["day"], clicks, registrations, conversion])
    safe = f"partner_report_{period}d.csv"
    return StreamingResponse(iter(["\ufeff" + output.getvalue()]), media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{safe}"', "Cache-Control": "no-store"})


@app.post("/supplier/partner-promotion")
def supplier_partner_promotion(request: Request, csrf: str = Form(...), publication_url: str = Form(...)):
    check_csrf(request, csrf); user = require_user(request, "supplier")
    from urllib.parse import urlparse
    publication_url = publication_url.strip()[:1000]
    parsed = urlparse(publication_url)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
        raise HTTPException(400, "Укажи корректную HTTPS-ссылку на публикацию")
    with connect() as con:
        con.execute("""INSERT INTO partner_promotion_sites(supplier_id,publication_url,status,review_note,reviewed_by,reviewed_at,submitted_at,updated_at)
            VALUES(?,?,'pending','',NULL,NULL,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)
            ON CONFLICT(supplier_id) DO UPDATE SET publication_url=excluded.publication_url,status='pending',review_note='',reviewed_by=NULL,reviewed_at=NULL,submitted_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP""", (user['id'], publication_url))
        con.commit()
    return RedirectResponse('/dashboard?partner_promotion=submitted', status_code=303)

@app.post("/admin/partner-rewards/policy")
def admin_partner_reward_policy(request: Request, csrf: str = Form(...), amount: int = Form(...)):
    check_csrf(request, csrf); require_user(request, "admin")
    if amount < 0 or amount > 100000:
        raise HTTPException(400, "Размер тестового бонуса должен быть от 0 до 100 000 ₽")
    with connect() as con:
        con.execute("INSERT INTO app_settings(key,value) VALUES('partner_reward_amount',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=CURRENT_TIMESTAMP", (str(amount),))
        con.commit()
    return RedirectResponse('/admin#partner-rewards',status_code=303)

@app.post("/admin/partner-rewards/{reward_id}/decision")
def admin_partner_reward_decision(request: Request, reward_id: int, csrf: str = Form(...), status: str = Form(...), note: str = Form("")):
    check_csrf(request, csrf); admin_user = require_user(request, "admin")
    if status not in ("approved", "reversed") or len(note) > 500:
        raise HTTPException(400, "Некорректное решение или комментарий")
    with connect() as con:
        con.execute("BEGIN IMMEDIATE")
        row = con.execute("SELECT * FROM partner_rewards WHERE id=?",(reward_id,)).fetchone()
        if not row: raise HTTPException(404,"Начисление не найдено")
        if row['status'] not in ('pending','approved') or (row['status']=='approved' and status!='reversed'):
            raise HTTPException(409,"Это начисление уже закрыто или не может быть изменено таким действием")
        con.execute("UPDATE partner_rewards SET status=?,admin_note=?,reviewed_by=?,reviewed_at=CURRENT_TIMESTAMP WHERE id=?",(status,note.strip(),admin_user['id'],reward_id))
        if status == 'reversed':
            con.execute("INSERT OR IGNORE INTO partner_reward_flags(flag_key,reward_id,supplier_id,advertiser_id,severity,details,status,reviewed_by,reviewed_at) VALUES(?,?,?,?,? ,?,'reviewed',?,CURRENT_TIMESTAMP)",(f'admin_reversal:{reward_id}',reward_id,row['supplier_id'],row['advertiser_id'],'medium',note.strip() or 'Администратор отменил тестовое начисление.',admin_user['id']))
        con.commit()
    return RedirectResponse('/admin#partner-rewards',status_code=303)

@app.post("/admin/partner-rewards/{reward_id}/sandbox-paid")
def admin_partner_reward_sandbox_paid(request: Request, reward_id: int, csrf: str = Form(...), note: str = Form("")):
    check_csrf(request, csrf); admin_user = require_user(request, "admin")
    if len(note) > 500: raise HTTPException(400,"Комментарий слишком длинный")
    with connect() as con:
        con.execute("BEGIN IMMEDIATE")
        row = con.execute("SELECT * FROM partner_rewards WHERE id=?",(reward_id,)).fetchone()
        if not row: raise HTTPException(404,"Начисление не найдено")
        if row['status'] != 'approved': raise HTTPException(409,"Для тестовой отметки сначала одобри начисление")
        con.execute("UPDATE partner_rewards SET status='paid_sandbox',admin_note=?,reviewed_by=?,reviewed_at=CURRENT_TIMESTAMP,paid_sandbox_at=CURRENT_TIMESTAMP WHERE id=?",(note.strip(),admin_user['id'],reward_id))
        con.commit()
    return RedirectResponse('/admin#partner-rewards',status_code=303)

@app.post("/admin/partner-reward-flags/{flag_id}/status")
def admin_partner_reward_flag_status(request: Request, flag_id: int, csrf: str = Form(...), status: str = Form(...)):
    check_csrf(request, csrf); admin_user = require_user(request, "admin")
    if status not in ('reviewed','dismissed'): raise HTTPException(400,"Недопустимый статус сигнала")
    with connect() as con:
        cur = con.execute("UPDATE partner_reward_flags SET status=?,reviewed_by=?,reviewed_at=CURRENT_TIMESTAMP WHERE id=? AND status='open'",(status,admin_user['id'],flag_id))
        if not cur.rowcount: raise HTTPException(404,"Открытый сигнал не найден")
        con.commit()
    return RedirectResponse('/admin#partner-reward-flags',status_code=303)

@app.post("/admin/partner-promotion/{site_id}/review")
def admin_partner_promotion_review(request: Request, site_id: int, csrf: str = Form(...), status: str = Form(...), note: str = Form("")):
    check_csrf(request, csrf); admin_user = require_user(request, "admin")
    if status not in ("approved", "rejected", "pending"):
        raise HTTPException(400, "Недопустимый статус")
    with connect() as con:
        row = con.execute("SELECT supplier_id FROM partner_promotion_sites WHERE id=?", (site_id,)).fetchone()
        if not row: raise HTTPException(404, "Заявка на продвижение не найдена")
        con.execute("UPDATE partner_promotion_sites SET status=?,review_note=?,reviewed_by=?,reviewed_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP WHERE id=?", (status, note.strip()[:1000], admin_user['id'], site_id))
        if status == "approved":
            notify(con, row['supplier_id'], "partner_promotion_approved", "Размещение ADMARKET подтверждено", "Теперь доступны публикация рекламных предложений и партнёрская статистика.", "/dashboard#partner-promotion", actor_id=admin_user['id'])
        elif status == "rejected":
            notify(con, row['supplier_id'], "partner_promotion_rejected", "Нужно исправить размещение ADMARKET", note.strip()[:500] or "Администратор отклонил подтверждение. Отправь новую ссылку на публикацию.", "/dashboard#partner-promotion", actor_id=admin_user['id'])
        con.commit()
    return RedirectResponse('/admin#partner-promotion', status_code=303)

@app.post("/supplier/platform-accounts")
def supplier_platform_account(request: Request, csrf: str = Form(...), platform: str = Form(...), handle: str = Form(...), profile_url: str = Form(...)):
    check_csrf(request, csrf); user=require_user(request,"supplier")
    from urllib.parse import urlparse
    platform=platform.strip()[:40]; handle=handle.strip()[:120]; profile_url=profile_url.strip()[:1000]
    parsed=urlparse(profile_url)
    if len(platform)<2 or len(handle)<2 or parsed.scheme!='https' or not parsed.netloc or parsed.username or parsed.password:
        raise HTTPException(400,"Укажи платформу, идентификатор и корректную HTTPS-ссылку без логина/пароля")
    with connect() as con:
        try:
            con.execute("INSERT INTO platform_accounts(supplier_id,platform,handle,profile_url) VALUES(?,?,?,?)",(user['id'],platform,handle,profile_url))
            con.commit()
        except Exception as exc:
            if 'UNIQUE' in str(exc): raise HTTPException(409,"Такой аккаунт уже добавлен")
            raise
    return RedirectResponse('/dashboard?platform=added',status_code=303)

@app.post("/supplier/platform-accounts/{account_id}/metrics")
def supplier_platform_metrics(request: Request, account_id: int, csrf: str = Form(...), followers: int = Form(...), avg_views: int = Form(...), avg_reach: int = Form(...), engagement_rate: float = Form(...), evidence_url: str = Form("")):
    check_csrf(request,csrf); user=require_user(request,"supplier")
    from urllib.parse import urlparse
    if min(followers,avg_views,avg_reach)<0 or max(followers,avg_views,avg_reach)>2_000_000_000 or not 0<=engagement_rate<=100:
        raise HTTPException(400,"Проверь значения статистики")
    evidence_url=evidence_url.strip()[:1000]
    if evidence_url:
        parsed=urlparse(evidence_url)
        if parsed.scheme!='https' or not parsed.netloc or parsed.username or parsed.password: raise HTTPException(400,"Ссылка на подтверждение должна использовать HTTPS")
    with connect() as con:
        account=con.execute("SELECT id FROM platform_accounts WHERE id=? AND supplier_id=?",(account_id,user['id'])).fetchone()
        if not account: raise HTTPException(404,"Аккаунт площадки не найден")
        cur=con.execute("INSERT INTO platform_metric_snapshots(account_id,followers,avg_views,avg_reach,engagement_rate,evidence_url,created_by) VALUES(?,?,?,?,?,?,?)",(account_id,followers,avg_views,avg_reach,engagement_rate,evidence_url,user['id']))
        assessment=assess_account(con,account_id)
        con.commit()
    return RedirectResponse('/dashboard?metrics=added',status_code=303)

@app.post("/admin/platform-accounts/{account_id}/verification")
def admin_platform_verification(request: Request, account_id: int, csrf: str = Form(...), status: str = Form(...), note: str = Form("")):
    check_csrf(request,csrf); admin_user=require_user(request,"admin")
    if status not in ('verified','rejected','pending'): raise HTTPException(400,"Недопустимый статус")
    with connect() as con:
        cur=con.execute("UPDATE platform_accounts SET verification_status=?,verification_note=?,verified_by=?,verified_at=CASE WHEN ?='verified' THEN CURRENT_TIMESTAMP ELSE '' END,updated_at=CURRENT_TIMESTAMP WHERE id=?",(status,note.strip()[:500],admin_user['id'],status,account_id))
        if cur.rowcount==0: raise HTTPException(404,"Аккаунт площадки не найден")
        con.commit()
    return RedirectResponse('/admin#platform-trust',status_code=303)

@app.post("/admin/platform-trust/scan")
def admin_platform_trust_scan(request: Request, csrf: str = Form(...)):
    check_csrf(request,csrf); require_user(request,"admin")
    with connect() as con:
        accounts=con.execute("SELECT id FROM platform_accounts WHERE EXISTS(SELECT 1 FROM platform_metric_snapshots ms WHERE ms.account_id=platform_accounts.id)").fetchall()
        count=0
        for row in accounts:
            if assess_account(con,row['id']): count+=1
        con.commit()
    return RedirectResponse('/admin#platform-trust',status_code=303)

@app.post("/admin/platform-trust-signals/{signal_id}/status")
def admin_platform_trust_signal_status(request: Request, signal_id: int, csrf: str = Form(...), status: str = Form(...), note: str = Form("")):
    check_csrf(request,csrf); admin_user=require_user(request,"admin")
    if status not in ('reviewed','dismissed'): raise HTTPException(400,"Недопустимый статус")
    with connect() as con:
        cur=con.execute("UPDATE platform_trust_signals SET status=?,reviewed_by=?,reviewed_at=CURRENT_TIMESTAMP,review_note=? WHERE id=?",(status,admin_user['id'],note.strip()[:500],signal_id))
        if cur.rowcount==0: raise HTTPException(404,"Сигнал не найден")
        con.commit()
    return RedirectResponse('/admin#platform-trust',status_code=303)

@app.get("/admin/platform-trust.json")
def admin_platform_trust_json(request: Request):
    require_user(request,"admin")
    with connect() as con:
        accounts=[dict(r) for r in con.execute("""SELECT pa.id,pa.supplier_id,pa.platform,pa.handle,pa.profile_url,pa.verification_status,
            (SELECT trust_score FROM platform_trust_assessments a WHERE a.account_id=pa.id ORDER BY a.id DESC LIMIT 1) AS trust_score,
            (SELECT confidence FROM platform_trust_assessments a WHERE a.account_id=pa.id ORDER BY a.id DESC LIMIT 1) AS confidence
            FROM platform_accounts pa ORDER BY pa.id DESC""").fetchall()]
        signals=[dict(r) for r in con.execute("SELECT id,account_id,signal_type,severity,details,status,created_at FROM platform_trust_signals ORDER BY id DESC LIMIT 500").fetchall()]
    return {"version":"2.8.0","method":"heuristic manual-review triage; supplier-submitted metrics; no external API verification","accounts":accounts,"signals":signals}

@app.get("/admin/finance.csv")
def admin_finance_csv(request: Request):
    require_user(request, "admin")
    with connect() as con:
        rows = con.execute("""SELECT fe.id,fe.created_at,ar.title AS request_title,au.email AS advertiser_email,
            su.email AS supplier_email,fe.base_amount,fe.commission_rate,fe.commission_amount,
            fe.supplier_estimated_net,fe.advertiser_estimated_total,pt.version
            FROM financial_estimates fe JOIN placement_terms pt ON pt.id=fe.terms_id
            JOIN placements p ON p.id=fe.placement_id JOIN request_responses rr ON rr.id=p.response_id
            JOIN advertiser_requests ar ON ar.id=rr.request_id
            JOIN users au ON au.id=fe.advertiser_id JOIN users su ON su.id=fe.supplier_id
            ORDER BY fe.id DESC""").fetchall()
    output = io.StringIO(newline="")
    writer = csv.writer(output, delimiter=";")
    writer.writerow(["ID оценки","Дата","Заявка","Рекламодатель","Поставщик","Сумма размещения ₽","Комиссия %","Оценка комиссии ₽","Оценка поставщику ₽","Оценка бюджета рекламодателя ₽","Версия условий"])
    for row in rows: writer.writerow(list(row))
    data = "\ufeff" + output.getvalue()
    return StreamingResponse(iter([data]), media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition":"attachment; filename=admarket_finance_estimates.csv"})

@app.get("/admin/reconciliation")
def admin_reconciliation(request: Request):
    require_user(request, "admin")
    return JSONResponse(reconcile_financial_state())

@app.post("/admin/settings/commission")
def admin_set_commission(request: Request, csrf: str = Form(...), rate: float = Form(...)):
    check_csrf(request, csrf); require_user(request, "admin")
    if not 0 <= rate <= 30: raise HTTPException(400, "Комиссия должна быть от 0 до 30%")
    with connect() as con:
        con.execute("INSERT INTO app_settings(key,value,updated_at) VALUES('commission_rate',?,CURRENT_TIMESTAMP) ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=CURRENT_TIMESTAMP", (str(round(rate,2)),))
        con.commit()
    return RedirectResponse("/admin#finance", status_code=303)

@app.post("/admin/offers/{offer_id}/status")
def admin_offer_status(request: Request, offer_id: int, csrf: str = Form(...), status: str = Form(...)):
    check_csrf(request, csrf); require_user(request, "admin")
    if status not in ("approved", "rejected", "pending"): raise HTTPException(400, "Недопустимый статус")
    with connect() as con:
        cur = con.execute("UPDATE supplier_offers SET status=? WHERE id=?", (status, offer_id)); con.commit()
        if cur.rowcount == 0: raise HTTPException(404, "Предложение не найдено")
    return RedirectResponse("/admin#offers", status_code=303)

@app.post("/admin/users/{user_id}/active")
def admin_user_active(request: Request, user_id: int, csrf: str = Form(...), is_active: int = Form(...)):
    check_csrf(request, csrf); admin_user = require_user(request, "admin")
    if user_id == admin_user["id"]: raise HTTPException(400, "Нельзя отключить собственный аккаунт")
    if is_active not in (0,1): raise HTTPException(400, "Недопустимое значение")
    with connect() as con:
        cur = con.execute("UPDATE users SET is_active=? WHERE id=? AND role!='admin'", (is_active,user_id)); con.commit()
        if cur.rowcount == 0: raise HTTPException(404, "Пользователь не найден или это администратор")
    return RedirectResponse("/admin#users", status_code=303)


@app.get("/admin/quality.json")
def admin_quality_json(request: Request):
    require_user(request, "admin")
    with connect() as con:
        rows=[]
        for supplier in con.execute("SELECT id,email FROM users WHERE role='supplier' ORDER BY id").fetchall():
            q=supplier_quality(con,supplier['id'])
            if q: q['email']=supplier['email']; rows.append(q)
    return {"version":"2.8.0","scoring":"40% ratings, 30% SLA, 20% open complaints, 10% verification","suppliers":rows}

@app.post("/admin/quality/recalculate")
def admin_quality_recalculate(request: Request, csrf: str = Form(...)):
    check_csrf(request,csrf); require_user(request,"admin")
    with connect() as con:
        count=0
        for supplier in con.execute("SELECT id FROM users WHERE role='supplier'").fetchall():
            q=supplier_quality(con,supplier['id'])
            if not q: continue
            con.execute("INSERT INTO supplier_quality_snapshots(supplier_id,score,rating_score,sla_score,complaint_score,verification_score,review_count,sla_count,complaint_count) VALUES(?,?,?,?,?,?,?,?,?)",
                (q['supplier_id'],q['score'],q['rating_score'],q['sla_score'],q['complaint_score'],q['verification_score'],q['review_count'],q['sla_count'],q['complaint_count']))
            count+=1
        con.commit()
    return RedirectResponse('/admin#quality',status_code=303)

@app.post("/admin/fraud/scan")
def admin_fraud_scan(request: Request, csrf: str = Form(...)):
    check_csrf(request,csrf); require_user(request,"admin")
    with connect() as con:
        created=scan_fraud_signals(con); con.commit()
    return RedirectResponse('/admin#fraud',status_code=303)

@app.post("/admin/fraud-signals/{signal_id}/status")
def admin_fraud_signal_status(request: Request, signal_id: int, csrf: str = Form(...), status: str = Form(...)):
    check_csrf(request,csrf); require_user(request,"admin")
    if status not in ('reviewed','dismissed'): raise HTTPException(400,'Недопустимый статус')
    with connect() as con:
        cur=con.execute("UPDATE fraud_review_signals SET status=?,reviewed_at=CURRENT_TIMESTAMP WHERE id=?",(status,signal_id))
        con.commit()
        if cur.rowcount==0: raise HTTPException(404,'Сигнал не найден')
    return RedirectResponse('/admin#fraud',status_code=303)

@app.get("/analytics", response_class=HTMLResponse)
def analytics(request: Request):
    user = require_user(request)
    with connect() as con:
        if user['role'] == 'advertiser':
            scope = "ar.advertiser_id=?"; params = (user['id'],)
            requests_count = con.execute("SELECT COUNT(*) FROM advertiser_requests WHERE advertiser_id=?",params).fetchone()[0]
        elif user['role'] == 'supplier':
            scope = "rr.supplier_id=?"; params = (user['id'],)
            requests_count = con.execute("SELECT COUNT(DISTINCT rr.request_id) FROM request_responses rr WHERE rr.supplier_id=?",params).fetchone()[0]
        else:
            scope = "1=1"; params = ()
            requests_count = con.execute("SELECT COUNT(*) FROM advertiser_requests").fetchone()[0]
        rows = con.execute(f"""SELECT p.id,p.status,p.created_at,ar.title,ar.geo,pt.price,
            au.company AS advertiser_company,su.company AS supplier_company,pi.status AS payment_status,
            pi.escrow_status,pi.amount AS paid_amount
            FROM placements p JOIN request_responses rr ON rr.id=p.response_id
            JOIN advertiser_requests ar ON ar.id=rr.request_id
            JOIN users au ON au.id=ar.advertiser_id JOIN users su ON su.id=rr.supplier_id
            LEFT JOIN placement_terms pt ON pt.placement_id=p.id AND pt.status='accepted'
            LEFT JOIN payment_intents pi ON pi.placement_id=p.id
            WHERE {scope} ORDER BY p.id DESC LIMIT 250""",params).fetchall()
        avg_rating = con.execute("SELECT AVG(r.rating) FROM reviews r JOIN placements p ON p.id=r.placement_id JOIN request_responses rr ON rr.id=p.response_id JOIN advertiser_requests ar ON ar.id=rr.request_id WHERE " + ("ar.advertiser_id=?" if user['role']=='advertiser' else "rr.supplier_id=?" if user['role']=='supplier' else "1=1"), params).fetchone()[0]
    total = len(rows); completed = sum(1 for r in rows if r['status']=='completed')
    held = sum((r['paid_amount'] or 0) for r in rows if r['escrow_status']=='held')
    paid = sum((r['paid_amount'] or 0) for r in rows if r['payment_status']=='succeeded')
    return page(request,'analytics.html',rows=rows,requests_count=requests_count,total=total,completed=completed,
                held=held,paid=paid,avg_rating=round(avg_rating,2) if avg_rating is not None else None)

@app.get("/wallet", response_class=HTMLResponse)
def wallet(request: Request):
    user = require_user(request)
    with connect() as con:
        balance = con.execute("SELECT COALESCE(SUM(amount),0) FROM ledger_entries WHERE account_type='user' AND user_id=?", (user['id'],)).fetchone()[0]
        rows = con.execute("""SELECT lt.*,le.amount FROM ledger_entries le JOIN ledger_transactions lt ON lt.id=le.transaction_id
            WHERE le.account_type='user' AND le.user_id=? ORDER BY le.id DESC LIMIT 100""", (user['id'],)).fetchall()
    return page(request, "wallet.html", balance=balance, transactions=rows)

@app.post("/advertiser/placements/{placement_id}/payments/test-intent")
def create_test_payment(request: Request, placement_id: int, csrf: str = Form(...)):
    check_csrf(request, csrf); user = require_user(request, "advertiser")
    if os.getenv("PAYMENT_MODE", "test").lower() != "test":
        raise HTTPException(503, "Платёжный режим отключён. В v0.9 поддерживается только sandbox.")
    with connect() as con:
        row = con.execute("""SELECT p.id,pt.id AS terms_id,pt.price,fe.commission_rate,fe.commission_amount,fe.supplier_estimated_net,rr.supplier_id
            FROM placements p JOIN placement_terms pt ON pt.placement_id=p.id AND pt.status='accepted'
            JOIN financial_estimates fe ON fe.terms_id=pt.id JOIN request_responses rr ON rr.id=p.response_id
            JOIN advertiser_requests ar ON ar.id=rr.request_id
            WHERE p.id=? AND ar.advertiser_id=? AND p.status IN ('confirmed','published','completed')
            ORDER BY pt.version DESC LIMIT 1""", (placement_id,user['id'])).fetchone()
        if not row: raise HTTPException(404, "Согласованные условия для оплаты не найдены")
        existing = con.execute("SELECT id,status FROM payment_intents WHERE terms_id=?", (row['terms_id'],)).fetchone()
        if existing:
            return RedirectResponse('/wallet', status_code=303)
        ref = 'sandbox_' + secrets.token_urlsafe(18)
        con.execute("""INSERT INTO payment_intents(placement_id,terms_id,advertiser_id,supplier_id,amount,commission_rate,commission_amount,supplier_amount,provider_reference)
            VALUES(?,?,?,?,?,?,?,?,?)""", (placement_id,row['terms_id'],user['id'],row['supplier_id'],row['price'],row['commission_rate'],row['commission_amount'],row['supplier_estimated_net'],ref))
        pid = con.execute("SELECT last_insert_rowid()").fetchone()[0]
        con.execute("INSERT INTO risk_events(user_id,placement_id,event_type,severity,details) VALUES(?,?,?,?,?)", (user['id'],placement_id,'sandbox_payment_intent','low',f'Test payment intent {pid} created'))
        con.commit()
    return RedirectResponse(f"/payments/test/{pid}", status_code=303)

@app.get("/payments/test/{payment_id}", response_class=HTMLResponse)
def test_payment_page(request: Request, payment_id: int):
    user = require_user(request, "advertiser")
    with connect() as con:
        row = con.execute("SELECT * FROM payment_intents WHERE id=? AND advertiser_id=?", (payment_id,user['id'])).fetchone()
    if not row: raise HTTPException(404, "Платёж не найден")
    csrf = csrf_token(request)
    html = f"""<!doctype html><html lang='ru'><meta charset='utf-8'><title>Sandbox checkout</title><body style='font:16px sans-serif;max-width:620px;margin:50px auto;padding:20px'><h1>Тестовая оплата ADMARKET</h1><p><b>Это имитация.</b> Деньги не списываются, платёжный провайдер не вызывается.</p><p>Сумма: {row['amount']} ₽ · Статус: {row['status']}</p><form method='post' action='/payments/test/{payment_id}/simulate'><input type='hidden' name='csrf' value='{csrf}'><button name='result' value='success'>Симулировать успешный платёж</button> <button name='result' value='fail'>Симулировать отказ</button></form><p><a href='/wallet'>Вернуться в кошелёк</a></p></body></html>"""
    return HTMLResponse(html)

@app.post("/payments/test/{payment_id}/simulate")
def simulate_test_payment(request: Request, payment_id: int, csrf: str = Form(...), result: str = Form(...)):
    check_csrf(request, csrf); user = require_user(request, "advertiser")
    if os.getenv("PAYMENT_MODE", "test").lower() != "test": raise HTTPException(403, "Sandbox выключен")
    if result not in ('success','fail'): raise HTTPException(400, "Недопустимый результат")
    with connect() as con:
        row = con.execute("SELECT * FROM payment_intents WHERE id=? AND advertiser_id=?", (payment_id,user['id'])).fetchone()
    if not row: raise HTTPException(404, "Платёж не найден")
    secret = os.getenv("SANDBOX_WEBHOOK_SECRET", "local-sandbox-webhook-secret-change-me")
    payload = {"event_id": "sim_" + secrets.token_urlsafe(18), "type": "payment.succeeded" if result == 'success' else "payment.failed", "payment_id": payment_id}
    raw = json.dumps(payload, separators=(",",":"), sort_keys=True).encode("utf-8")
    signature = hmac.new(secret.encode("utf-8"), raw, hashlib.sha256).hexdigest()
    process_signed_sandbox_webhook(payload, raw, signature, secret)
    return RedirectResponse('/wallet', status_code=303)

@app.post("/webhooks/sandbox")
async def sandbox_webhook(request: Request):
    if os.getenv("PAYMENT_MODE", "test").lower() != "test": raise HTTPException(403, "Sandbox webhook выключен")
    secret = os.getenv("SANDBOX_WEBHOOK_SECRET", "")
    if len(secret) < 24: raise HTTPException(503, "SANDBOX_WEBHOOK_SECRET не настроен")
    raw = await request.body()
    signature = request.headers.get("X-ADMARKET-SIGNATURE", "")
    if len(raw) > 65536: raise HTTPException(413, "Webhook слишком большой")
    try: payload = json.loads(raw.decode("utf-8"))
    except Exception: raise HTTPException(400, "Некорректный JSON")
    if not isinstance(payload, dict): raise HTTPException(400, "Ожидался JSON-объект")
    result = process_signed_sandbox_webhook(payload, raw, signature, secret)
    return {"accepted":True,"result":result}

@app.post("/admin/payments/{payment_id}/release")
def admin_release_escrow(request: Request, payment_id: int, csrf: str = Form(...), note: str = Form("")):
    check_csrf(request, csrf); admin_user = require_user(request, 'admin')
    if len(note) > 500: raise HTTPException(400, 'Комментарий слишком длинный')
    with connect() as con:
        con.execute("BEGIN IMMEDIATE")
        row = con.execute("SELECT pi.*,p.status AS placement_status FROM payment_intents pi JOIN placements p ON p.id=pi.placement_id WHERE pi.id=? AND pi.mode='test'", (payment_id,)).fetchone()
        if not row: raise HTTPException(404, 'Sandbox-платёж не найден')
        if row['status'] != 'succeeded' or row['escrow_status'] != 'held': raise HTTPException(409, 'В эскроу нет средств для выпуска')
        if row['placement_status'] != 'completed': raise HTTPException(409, 'Выпуск возможен только после отметки о завершении размещения')
        disputes = con.execute("SELECT status FROM disputes WHERE placement_id=? ORDER BY id DESC", (row['placement_id'],)).fetchall()
        if any(d['status'] in ('open','in_review') for d in disputes): raise HTTPException(409, 'Выпуск заморожен: по размещению открыт спор')
        if disputes and disputes[0]['status'] != 'resolved_for_supplier': raise HTTPException(409, 'Для выпуска после спора требуется решение в пользу поставщика')
        if con.execute("SELECT 1 FROM escrow_events WHERE payment_intent_id=? AND event_type='funds_released'", (payment_id,)).fetchone(): raise HTTPException(409, 'Выпуск по этому платежу уже выполнен')
        txid = con.execute("INSERT INTO ledger_transactions(payment_intent_id,kind,description) VALUES(?,'sandbox_payment',?)", (payment_id,'Sandbox: выпуск из эскроу')).lastrowid
        entries = [('external',None,-row['amount']),('user',row['supplier_id'],row['supplier_amount']),('platform',None,row['commission_amount'])]
        if sum(x[2] for x in entries) != 0: raise HTTPException(500,'Нарушен баланс выпуска')
        con.executemany("INSERT INTO ledger_entries(transaction_id,account_type,user_id,amount) VALUES(?,?,?,?)",[(txid,*e) for e in entries])
        con.execute("UPDATE payment_intents SET escrow_status='released' WHERE id=?",(payment_id,))
        con.execute("INSERT INTO escrow_events(payment_intent_id,actor_id,event_type,amount,note) VALUES(?,?,'funds_released',?,?)",(payment_id,admin_user['id'],row['amount'],note.strip()))
        accrue_partner_reward(con, payment_id)
        con.commit()
    return RedirectResponse('/admin#payments',status_code=303)

@app.post("/admin/payments/{payment_id}/refund")
def admin_refund(request: Request, payment_id: int, csrf: str = Form(...), reason: str = Form(...)):
    check_csrf(request, csrf); admin_user = require_user(request, 'admin')
    reason = reason.strip()
    if not reason or len(reason)>500: raise HTTPException(400, 'Укажи причину возврата (до 500 символов)')
    with connect() as con:
        con.execute("BEGIN IMMEDIATE")
        row = con.execute("SELECT * FROM payment_intents WHERE id=?", (payment_id,)).fetchone()
        if not row: raise HTTPException(404, 'Платёж не найден')
        if row['mode'] != 'test' or row['status'] != 'succeeded': raise HTTPException(409, 'Возврат возможен только для успешной тестовой оплаты')
        originals = con.execute("SELECT id FROM ledger_transactions WHERE payment_intent_id=?", (payment_id,)).fetchall()
        if not originals: raise HTTPException(409, 'Исходные проводки не найдены')
        if con.execute("SELECT 1 FROM refunds WHERE payment_intent_id=?", (payment_id,)).fetchone(): raise HTTPException(409, 'Возврат уже оформлен')
        txid = con.execute("INSERT INTO ledger_transactions(refund_of,kind,description) VALUES(?,'refund',?)", (originals[0]['id'],'Тестовый возврат: '+reason)).lastrowid
        source = con.execute("SELECT account_type,user_id,SUM(amount) AS amount FROM ledger_entries WHERE transaction_id IN (%s) GROUP BY account_type,user_id" % ",".join("?" for _ in originals), [r['id'] for r in originals]).fetchall()
        con.executemany("INSERT INTO ledger_entries(transaction_id,account_type,user_id,amount) VALUES(?,?,?,?)", [(txid,e['account_type'],e['user_id'],-e['amount']) for e in source if e['amount']])
        con.execute("INSERT INTO refunds(payment_intent_id,ledger_transaction_id,amount,reason) VALUES(?,?,?,?)", (payment_id,txid,row['amount'],reason))
        con.execute("UPDATE payment_intents SET status='refunded',escrow_status='refunded' WHERE id=?", (payment_id,))
        reward = con.execute("SELECT * FROM partner_rewards WHERE payment_intent_id=?",(payment_id,)).fetchone()
        if reward and reward['status'] in ('pending','approved'):
            con.execute("UPDATE partner_rewards SET status='reversed',admin_note=?,reviewed_by=?,reviewed_at=CURRENT_TIMESTAMP WHERE id=?",('Автоматическая отмена начисления из-за sandbox-возврата: '+reason[:400],admin_user['id'],reward['id']))
        elif reward and reward['status']=='paid_sandbox':
            con.execute("INSERT OR IGNORE INTO partner_reward_flags(flag_key,reward_id,supplier_id,advertiser_id,severity,details) VALUES(?,?,?,?,?,?)",(f'refund_after_sandbox_reward:{reward["id"]}',reward['id'],reward['supplier_id'],reward['advertiser_id'],'high','Тестовый возврат оформлен после отметки партнёрского бонуса как выплаченного в sandbox. Требуется ручная проверка; реального перевода не было.'))
        con.execute("INSERT INTO escrow_events(payment_intent_id,actor_id,event_type,amount,note) VALUES(?,?,'funds_refunded',?,?)",(payment_id,admin_user['id'],row['amount'],reason))
        con.execute("INSERT INTO risk_events(user_id,placement_id,event_type,severity,details) VALUES(?,?,?,?,?)", (admin_user['id'],row['placement_id'],'refund_issued','medium',f'Test refund for payment {payment_id}'))
        con.commit()
    return RedirectResponse('/admin#payments', status_code=303)

@app.post("/placements/{placement_id}/disputes")
def open_dispute(request: Request, placement_id: int, csrf: str = Form(...), reason: str = Form(...)):
    check_csrf(request, csrf); user = require_user(request)
    reason = reason.strip()
    if len(reason) < 10 or len(reason) > 3000: raise HTTPException(400, "Опиши проблему минимум 10 и максимум 3000 символов")
    with connect() as con:
        con.execute("BEGIN IMMEDIATE")
        row = con.execute("""SELECT p.id,ar.advertiser_id,rr.supplier_id FROM placements p
            JOIN request_responses rr ON rr.id=p.response_id JOIN advertiser_requests ar ON ar.id=rr.request_id
            WHERE p.id=? AND (ar.advertiser_id=? OR rr.supplier_id=?)""", (placement_id,user['id'],user['id'])).fetchone()
        if not row: raise HTTPException(404,"Размещение не найдено")
        if user['role'] not in ('advertiser','supplier'): raise HTTPException(403,"Недостаточно прав")
        if con.execute("SELECT 1 FROM disputes WHERE placement_id=? AND status IN ('open','in_review')",(placement_id,)).fetchone():
            raise HTTPException(409,"По этому размещению уже есть активный спор")
        cur=con.execute("INSERT INTO disputes(placement_id,opened_by,reason) VALUES(?,?,?)",(placement_id,user['id'],reason))
        did=cur.lastrowid
        other=row['supplier_id'] if user['id']==row['advertiser_id'] else row['advertiser_id']
        notify(con,other,'dispute_opened','Открыт спор по размещению',f'Спор #{did}: {reason[:220]}','/dashboard#placements',placement_id,user['id'])
        con.execute("INSERT INTO risk_events(user_id,placement_id,event_type,severity,details) VALUES(?,?,?,?,?)",(user['id'],placement_id,'placement_dispute_opened','medium',f'Dispute #{did} opened'))
        con.commit()
    return RedirectResponse('/dashboard#placements',status_code=303)

@app.post("/disputes/{dispute_id}/evidence/link")
def add_dispute_link(request: Request, dispute_id: int, csrf: str = Form(...), url: str = Form(...), note: str = Form('')):
    check_csrf(request,csrf); user=require_user(request); url=url.strip(); note=note.strip()
    if len(url)>2000 or not re.match(r'^https?://',url,re.I) or len(note)>500: raise HTTPException(400,"Нужна ссылка http/https и комментарий до 500 символов")
    with connect() as con:
        row=con.execute("""SELECT d.id FROM disputes d JOIN placements p ON p.id=d.placement_id
            JOIN request_responses rr ON rr.id=p.response_id JOIN advertiser_requests ar ON ar.id=rr.request_id
            WHERE d.id=? AND d.status IN ('open','in_review') AND (ar.advertiser_id=? OR rr.supplier_id=?)""",(dispute_id,user['id'],user['id'])).fetchone()
        if not row: raise HTTPException(404,"Активный спор не найден")
        con.execute("INSERT INTO dispute_evidence(dispute_id,uploaded_by,evidence_type,external_url,note) VALUES(?,?,'link',?,?)",(dispute_id,user['id'],url,note))
        con.commit()
    return RedirectResponse('/dashboard#placements',status_code=303)

@app.post("/disputes/{dispute_id}/evidence/file")
async def upload_dispute_evidence(request: Request, dispute_id: int, csrf: str = Form(...), file: UploadFile = File(...), note: str = Form('')):
    check_csrf(request,csrf); user=require_user(request); note=note.strip()
    if len(note)>500: raise HTTPException(400,"Комментарий слишком длинный")
    allowed={'image/jpeg','image/png','image/webp','application/pdf'}
    if file.content_type not in allowed: raise HTTPException(400,"Разрешены только JPG, PNG, WEBP и PDF")
    with connect() as con:
        row=con.execute("""SELECT d.id FROM disputes d JOIN placements p ON p.id=d.placement_id
            JOIN request_responses rr ON rr.id=p.response_id JOIN advertiser_requests ar ON ar.id=rr.request_id
            WHERE d.id=? AND d.status IN ('open','in_review') AND (ar.advertiser_id=? OR rr.supplier_id=?)""",(dispute_id,user['id'],user['id'])).fetchone()
    if not row: raise HTTPException(404,"Активный спор не найден")
    content=await file.read(8*1024*1024+1)
    if not content or len(content)>8*1024*1024: raise HTTPException(413,"Файл должен быть не пустым и не больше 8 МБ")
    # Verify magic bytes rather than trusting the browser-supplied MIME type.
    signatures={'image/jpeg':content.startswith(b'\xff\xd8\xff'),'image/png':content.startswith(b'\x89PNG\r\n\x1a\n'),
                'image/webp':len(content)>12 and content[:4]==b'RIFF' and content[8:12]==b'WEBP','application/pdf':content.startswith(b'%PDF-')}
    if not signatures.get(file.content_type): raise HTTPException(400,"Содержимое файла не соответствует типу")
    digest=hashlib.sha256(content).hexdigest(); ext={'image/jpeg':'.jpg','image/png':'.png','image/webp':'.webp','application/pdf':'.pdf'}[file.content_type]
    stored=secrets.token_hex(24)+ext; evidence_dir=ROOT.parent/'data'/'dispute_evidence'; evidence_dir.mkdir(parents=True,exist_ok=True)
    (evidence_dir/stored).write_bytes(content)
    original=re.sub(r'[^A-Za-zА-Яа-я0-9_. -]','_',Path(file.filename or 'evidence').name)[:180]
    with connect() as con:
        duplicate=con.execute("SELECT id,uploaded_by FROM dispute_evidence WHERE sha256=? AND sha256!=''",(digest,)).fetchone()
        con.execute("INSERT INTO dispute_evidence(dispute_id,uploaded_by,evidence_type,original_name,stored_name,mime_type,size_bytes,sha256,note) VALUES(?,?,'file',?,?,?,?,?,?)",
            (dispute_id,user['id'],original,stored,file.content_type,len(content),digest,note))
        if duplicate and duplicate['uploaded_by']!=user['id']:
            con.execute("INSERT INTO risk_events(user_id,event_type,severity,details) VALUES(?,?,?,?)",(user['id'],'duplicate_dispute_evidence','medium',f'Reused evidence hash in dispute #{dispute_id}; previous evidence #{duplicate["id"]}'))
        con.commit()
    return RedirectResponse('/dashboard#placements',status_code=303)

@app.get("/disputes/evidence/{evidence_id}")
def download_dispute_evidence(request: Request, evidence_id: int):
    user=require_user(request)
    with connect() as con:
        row=con.execute("""SELECT de.*,d.placement_id,ar.advertiser_id,rr.supplier_id FROM dispute_evidence de
            JOIN disputes d ON d.id=de.dispute_id JOIN placements p ON p.id=d.placement_id
            JOIN request_responses rr ON rr.id=p.response_id JOIN advertiser_requests ar ON ar.id=rr.request_id
            WHERE de.id=?""",(evidence_id,)).fetchone()
    if not row: raise HTTPException(404,"Материал не найден")
    if not is_authorized_admin(user) and user['id'] not in (row['advertiser_id'],row['supplier_id']): raise HTTPException(403,"Нет доступа к материалу")
    if row['evidence_type']!='file': raise HTTPException(404,"Это внешняя ссылка")
    path=ROOT.parent/'data'/'dispute_evidence'/row['stored_name']
    if not path.is_file(): raise HTTPException(404,"Файл недоступен")
    return FileResponse(path,media_type=row['mime_type'],filename=row['original_name'],headers={'X-Content-Type-Options':'nosniff','Content-Security-Policy':"default-src 'none'; sandbox"})

@app.post("/admin/disputes/{dispute_id}/decision")
def admin_dispute_decision(request: Request, dispute_id: int, csrf: str = Form(...), decision: str = Form(...), resolution: str = Form(...)):
    check_csrf(request,csrf); admin_user=require_user(request,'admin'); resolution=resolution.strip()
    if decision not in ('in_review','resolved_for_advertiser','resolved_for_supplier','closed') or not resolution or len(resolution)>2000:
        raise HTTPException(400,"Выбери решение и укажи комментарий до 2000 символов")
    with connect() as con:
        con.execute("BEGIN IMMEDIATE")
        d=con.execute("SELECT * FROM disputes WHERE id=?",(dispute_id,)).fetchone()
        if not d: raise HTTPException(404,"Спор не найден")
        con.execute("UPDATE disputes SET status=?,resolution=?,reviewer_id=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",(decision,resolution,admin_user['id'],dispute_id))
        parties=con.execute("SELECT ar.advertiser_id,rr.supplier_id FROM placements p JOIN request_responses rr ON rr.id=p.response_id JOIN advertiser_requests ar ON ar.id=rr.request_id WHERE p.id=?",(d['placement_id'],)).fetchone()
        label={'in_review':'Передан на рассмотрение','resolved_for_advertiser':'Решён в пользу рекламодателя','resolved_for_supplier':'Решён в пользу поставщика','closed':'Закрыт без решения'}[decision]
        for uid in {parties['advertiser_id'],parties['supplier_id']}:
            notify(con,uid,'dispute_updated',f'Обновлён спор #{dispute_id}',f'{label}. {resolution[:350]}','/dashboard#placements',d['placement_id'],admin_user['id'])
        con.execute("INSERT INTO activity_log(actor_id,placement_id,event_type,summary) VALUES(?,?,?,?)",(admin_user['id'],d['placement_id'],'dispute_decision',f'Спор #{dispute_id}: {label}. {resolution[:350]}'))
        con.commit()
    return RedirectResponse('/admin#disputes',status_code=303)

@app.post("/admin/suppliers/{supplier_id}/verification")
def admin_supplier_verification(request: Request, supplier_id: int, csrf: str = Form(...), status: str = Form(...), note: str = Form('')):
    check_csrf(request, csrf); admin_user = require_user(request,'admin')
    if status not in ('pending','verified','rejected','unverified') or len(note)>500: raise HTTPException(400,'Некорректные данные')
    with connect() as con:
        cur=con.execute("UPDATE users SET supplier_verification=? WHERE id=? AND role='supplier'",(status,supplier_id))
        if not cur.rowcount: raise HTTPException(404,'Поставщик не найден')
        con.execute("INSERT INTO supplier_verification_events(supplier_id,admin_id,status,note) VALUES(?,?,?,?)",(supplier_id,admin_user['id'],status,note.strip()))
        con.commit()
    return RedirectResponse('/admin#users',status_code=303)

@app.post("/placements/{placement_id}/reviews")
def create_review(request: Request, placement_id: int, csrf: str = Form(...), rating: int = Form(...), body: str = Form('')):
    check_csrf(request,csrf); user=require_user(request)
    if rating<1 or rating>5 or len(body)>1500: raise HTTPException(400,'Оценка 1–5, текст до 1500 символов')
    with connect() as con:
        row=con.execute("""SELECT p.id,ar.advertiser_id,rr.supplier_id FROM placements p JOIN request_responses rr ON rr.id=p.response_id JOIN advertiser_requests ar ON ar.id=rr.request_id
            WHERE p.id=? AND p.status='completed' AND (ar.advertiser_id=? OR rr.supplier_id=?)""",(placement_id,user['id'],user['id'])).fetchone()
        if not row: raise HTTPException(404,'Завершённое размещение не найдено')
        if user['id']==row['advertiser_id']: reviewee=row['supplier_id']
        else: reviewee=row['advertiser_id']
        try: con.execute("INSERT INTO reviews(placement_id,reviewer_id,reviewee_id,rating,body) VALUES(?,?,?,?,?)",(placement_id,user['id'],reviewee,rating,body.strip()))
        except Exception as exc:
            if 'UNIQUE' in str(exc): raise HTTPException(409,'Отзыв по этому размещению уже оставлен')
            raise
        con.commit()
    return RedirectResponse('/dashboard#placements',status_code=303)

@app.post("/placements/{placement_id}/complaints")
def create_supplier_complaint(request: Request, placement_id: int, csrf: str = Form(...), category: str = Form(...), description: str = Form(...)):
    check_csrf(request,csrf); user=require_user(request)
    allowed={'late_delivery','quality','communication','misrepresentation','other'}
    description=description.strip()
    if category not in allowed or not 10 <= len(description) <= 2500:
        raise HTTPException(400,'Выберите категорию; описание должно быть от 10 до 2500 символов')
    with connect() as con:
        row=con.execute("""SELECT ar.advertiser_id,rr.supplier_id FROM placements p
            JOIN request_responses rr ON rr.id=p.response_id JOIN advertiser_requests ar ON ar.id=rr.request_id
            WHERE p.id=? AND (ar.advertiser_id=? OR rr.supplier_id=?)""",(placement_id,user['id'],user['id'])).fetchone()
        if not row: raise HTTPException(404,'Размещение не найдено')
        if user['id']==row['supplier_id']: raise HTTPException(403,'Поставщик не может подать жалобу на самого себя')
        cur=con.execute("INSERT INTO supplier_complaints(placement_id,opened_by,supplier_id,category,description) VALUES(?,?,?,?,?)",
            (placement_id,user['id'],row['supplier_id'],category,description))
        con.execute("INSERT INTO supplier_complaint_events(complaint_id,actor_id,event_type,details) VALUES(?,?,?,?)",(cur.lastrowid,user['id'],'opened',description[:500]))
        con.execute("INSERT INTO risk_events(user_id,placement_id,event_type,severity,details) VALUES(?,?,?,?,?)",(row['supplier_id'],placement_id,'supplier_complaint','medium',f'Complaint #{cur.lastrowid}: {category}'))
        con.commit()
    return RedirectResponse('/dashboard#placements',status_code=303)

@app.post("/admin/complaints/{complaint_id}/decision")
def admin_complaint_decision(request: Request, complaint_id: int, csrf: str = Form(...), status: str = Form(...), resolution: str = Form(...)):
    check_csrf(request,csrf); admin_user=require_user(request,'admin')
    if status not in ('in_review','resolved','dismissed') or not resolution.strip() or len(resolution)>2000:
        raise HTTPException(400,'Проверьте статус и обоснование решения')
    with connect() as con:
        cur=con.execute("UPDATE supplier_complaints SET status=?,resolution=?,reviewer_id=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",(status,resolution.strip(),admin_user['id'],complaint_id))
        if not cur.rowcount: raise HTTPException(404,'Жалоба не найдена')
        con.execute("INSERT INTO supplier_complaint_events(complaint_id,actor_id,event_type,details) VALUES(?,?,?,?,?)".replace('VALUES(?,?,?,?,?)','VALUES(?,?,?,?)'),(complaint_id,admin_user['id'],status,resolution.strip()))
        con.commit()
    return RedirectResponse('/admin#complaints',status_code=303)

@app.post("/admin/sla/scan")
def admin_sla_scan(request: Request, csrf: str = Form(...)):
    check_csrf(request,csrf); require_user(request,'admin')
    with connect() as con:
        cur=con.execute("""UPDATE supplier_sla_cases SET status='breached',breached_at=CURRENT_TIMESTAMP
            WHERE status='active' AND due_at<=CURRENT_TIMESTAMP""")
        count=cur.rowcount
        if count:
            con.execute("INSERT INTO risk_events(event_type,severity,details) VALUES('supplier_sla_breach','medium',?)",(f'Автоматическая проверка выявила просроченные SLA: {count}',))
        con.commit()
    return RedirectResponse('/admin#sla',status_code=303)

@app.post("/admin/moderation/{record_id}/review")
def admin_moderation_review(request: Request, record_id: int, csrf: str = Form(...), review_status: str = Form(...)):
    check_csrf(request,csrf); require_user(request,'admin')
    if review_status not in ('reviewed','dismissed'): raise HTTPException(400,'Недопустимый статус')
    with connect() as con:
        cur=con.execute("UPDATE moderation_records SET review_status=? WHERE id=?",(review_status,record_id)); con.commit()
        if not cur.rowcount: raise HTTPException(404,'Запись модерации не найдена')
    return RedirectResponse('/admin#moderation',status_code=303)

@app.post("/admin/risk-events/{event_id}/status")
def admin_risk_status(request: Request, event_id: int, csrf: str = Form(...), status: str = Form(...)):
    check_csrf(request,csrf); require_user(request,'admin')
    if status not in ('reviewed','dismissed'): raise HTTPException(400,'Недопустимый статус')
    with connect() as con:
        cur=con.execute("UPDATE risk_events SET status=? WHERE id=?",(status,event_id)); con.commit()
        if not cur.rowcount: raise HTTPException(404,'Событие не найдено')
    return RedirectResponse('/admin#risk',status_code=303)

@app.get("/api/inventory")
def api_inventory(channel: str = "", geo: str = "", q: str = ""):
    rows = inventory_rows()
    if channel: rows = [r for r in rows if r.get("channel") == channel]
    if geo: rows = [r for r in rows if r.get("geo") == geo or r.get("geo") == "Россия"]
    if q:
        needle = q.casefold()
        rows = [r for r in rows if needle in (str(r.get("name", "")) + " " + str(r.get("topic", ""))).casefold()]
    return rows

class Brief(BaseModel):
    business: str = ""; goal: str = ""; audience: str = ""; geo: str = "Россия"
    budget: int = Field(default=30000, ge=1000, le=100000000)
    channels: list[str] = Field(default_factory=list)
class ChatRequest(BaseModel):
    message: str
    history: list[dict[str,str]] = Field(default_factory=list)
    brief: Brief = Field(default_factory=Brief)

@app.post("/api/ai")
def ai_chat(payload: ChatRequest):
    message=payload.message.strip()
    if not message: return {"reply":"Расскажи, что за бизнес или продукт хочешь продвигать."}
    inventory=inventory_rows(); brief=payload.brief.model_dump()
    prior=' '.join(x.get('content','') for x in payload.history if x.get('role')=='user' and isinstance(x.get('content'),str))
    text=(prior+' '+message).strip()
    if not brief.get('goal') and not brief.get('business'): brief['goal']=text[:1200]
    if brief.get('geo')=='Россия':
        low=text.lower()
        if 'омск' in low: brief['geo']='Омск'
        elif 'сибир' in low: brief['geo']='Сибирь'
    money=re.search(r'(\d[\d\s]{2,})\s*(?:₽|руб(?:лей|ля|ль)?|р\b)',text.lower())
    money_short=re.search(r'(\d+)\s*(?:к|тыс(?:яч[а-я]*)?)',text.lower())
    if money: brief['budget']=int(re.sub(r'\s+','',money.group(1)))
    elif money_short: brief['budget']=int(money_short.group(1))*1000
    low=text.lower()
    detected=[n for n,ws in [('VK',['вк','вконтакте','vk']),('MAX',['max','макс']),('Email',['email','рассыл']),('Подкаст',['подкаст']),('Сайт / медиа',['сайт','медиа','портал'])] if any(w in low for w in ws)]
    if detected and not brief.get('channels'): brief['channels']=detected
    plan=build_plan(inventory,brief.get('goal','')+' '+brief.get('business',''),brief.get('audience',''),brief.get('geo','Россия'),brief.get('budget',30000),brief.get('channels',[]))
    return {"reply":ask_ai(message,payload.history,brief,plan['items']),"shortlist":plan['items'],"plan":plan}

@app.post("/api/plan")
def api_plan(brief: Brief):
    inventory=inventory_rows()
    return build_plan(inventory,brief.goal+' '+brief.business,brief.audience,brief.geo,brief.budget,brief.channels)

# Compatibility route: order submissions now require an authenticated advertiser.
@app.post("/orders")
def create_order(request: Request, csrf: str = Form(...), company: str = Form(...), contact: str = Form(...),
                 goal: str = Form(...), budget: int = Form(...), geo: str = Form(...), selected: str = Form(...)):
    check_csrf(request,csrf); user=require_user(request,"advertiser")
    with connect() as con:
        con.execute("INSERT INTO orders(company,contact,goal,budget,geo,selected,user_id) VALUES(?,?,?,?,?,?,?)",
            (company,contact,goal,budget,geo,selected,user['id'])); con.commit()
    return RedirectResponse("/dashboard?order=submitted",status_code=303)
