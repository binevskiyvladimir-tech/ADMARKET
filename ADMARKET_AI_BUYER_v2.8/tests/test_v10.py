import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from fastapi import HTTPException
from app import db
from app.main import process_sandbox_webhook, moderate_text

class V10Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        db.DATA = Path(self.tmp.name)
        db.DB_PATH = db.DATA / 'test.db'
        db.init_db()
        with db.connect() as con:
            con.execute("INSERT INTO users(email,password_hash,role) VALUES('buyer@x.test','x','advertiser')")
            con.execute("INSERT INTO users(email,password_hash,role) VALUES('seller@x.test','x','supplier')")
            con.execute("INSERT INTO advertiser_requests(advertiser_id,title,goal,geo,budget) VALUES(1,'Campaign','Leads','RU',5000)")
            con.execute("INSERT INTO request_responses(request_id,supplier_id,proposed_price,status) VALUES(1,2,1000,'accepted')")
            con.execute("INSERT INTO placements(response_id,status) VALUES(1,'completed')")
            con.execute("INSERT INTO placement_terms(placement_id,version,deliverables,price,proposed_by,status) VALUES(1,1,'Post',1000,2,'accepted')")
            con.execute("INSERT INTO payment_intents(placement_id,terms_id,advertiser_id,supplier_id,amount,commission_rate,commission_amount,supplier_amount,provider_reference,status) VALUES(1,1,1,2,1000,12,120,880,'sandbox_test_ref','created')")
            con.commit()

    def tearDown(self):
        self.tmp.cleanup()

    def test_v10_migration_is_additive_and_idempotent(self):
        db.init_db(); db.init_db()
        with db.connect() as con:
            tables={r['name'] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertTrue({'webhook_events','escrow_events','moderation_records'}.issubset(tables))
            cols={r[1] for r in con.execute('PRAGMA table_info(payment_intents)')}
            self.assertIn('escrow_status',cols)
            cols={r[1] for r in con.execute('PRAGMA table_info(moderation_records)')}
            self.assertIn('review_status',cols)

    def test_signed_event_processing_is_idempotent_and_holds_funds(self):
        payload={'event_id':'evt_test_000001','type':'payment.succeeded','payment_id':1}
        raw=json.dumps(payload,separators=(',',':'),sort_keys=True).encode()
        self.assertEqual(process_sandbox_webhook(payload,raw),'processed')
        self.assertEqual(process_sandbox_webhook(payload,raw),'duplicate')
        with db.connect() as con:
            self.assertEqual(con.execute('SELECT escrow_status FROM payment_intents WHERE id=1').fetchone()[0],'held')
            self.assertEqual(con.execute("SELECT SUM(amount) FROM ledger_entries WHERE account_type='user' AND user_id=1").fetchone()[0],-1000)
            self.assertEqual(con.execute("SELECT COALESCE(SUM(amount),0) FROM ledger_entries WHERE account_type='user' AND user_id=2").fetchone()[0],0)
            self.assertEqual(con.execute("SELECT SUM(amount) FROM ledger_entries WHERE account_type='external'").fetchone()[0],1000)
        changed={**payload,'type':'payment.failed'}
        changed_raw=json.dumps(changed,separators=(',',':'),sort_keys=True).encode()
        with self.assertRaises(HTTPException): process_sandbox_webhook(changed,changed_raw)

    def test_moderation_flags_suspicious_content_without_auto_rejection(self):
        with db.connect() as con:
            cur=con.execute("INSERT INTO supplier_offers(supplier_id,name,channel,topic,geo,price,audience,formats,details) VALUES(2,'Offer','Social','ads','RU',100,100,'post','')")
            offer_id=cur.lastrowid; con.commit()
        score,decision,reasons=moderate_text('supplier_offer',offer_id,{'name':'100% гарантия','details':'накрутка подписчиков','user_id':2})
        self.assertGreaterEqual(score,30)
        self.assertEqual(decision,'review')
        with db.connect() as con:
            self.assertEqual(con.execute('SELECT status FROM supplier_offers WHERE id=?',(offer_id,)).fetchone()[0],'pending')
            self.assertEqual(con.execute('SELECT decision FROM moderation_records WHERE entity_id=?',(offer_id,)).fetchone()[0],'review')

if __name__ == '__main__': unittest.main()
