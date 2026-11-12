import json
import tempfile
import unittest
from pathlib import Path
from app import db
from app.main import process_sandbox_webhook, reconcile_financial_state

class V11ReconciliationTests(unittest.TestCase):
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
            con.execute("INSERT INTO payment_intents(placement_id,terms_id,advertiser_id,supplier_id,amount,commission_rate,commission_amount,supplier_amount,provider_reference,status) VALUES(1,1,1,2,1000,12,120,880,'sandbox_ref','created')")
            con.commit()

    def tearDown(self):
        self.tmp.cleanup()

    def test_reconciliation_reports_clean_held_escrow(self):
        payload={'event_id':'evt_reconcile_001','type':'payment.succeeded','payment_id':1}
        raw=json.dumps(payload,separators=(',',':'),sort_keys=True).encode()
        self.assertEqual(process_sandbox_webhook(payload,raw),'processed')
        report=reconcile_financial_state()
        self.assertTrue(report['ok'], report['issues'])
        self.assertEqual(report['transactions_checked'],1)
        self.assertEqual(report['payments_checked'],1)

    def test_reconciliation_detects_unbalanced_transaction(self):
        payload={'event_id':'evt_reconcile_002','type':'payment.succeeded','payment_id':1}
        raw=json.dumps(payload,separators=(',',':'),sort_keys=True).encode()
        process_sandbox_webhook(payload,raw)
        with db.connect() as con:
            tx=con.execute("INSERT INTO ledger_transactions(payment_intent_id,kind,description) VALUES(1,'sandbox_payment','broken test row')").lastrowid
            con.execute("INSERT INTO ledger_entries(transaction_id,account_type,user_id,amount) VALUES(?,'user',1,-10)",(tx,))
            con.commit()
        report=reconcile_financial_state()
        self.assertFalse(report['ok'])
        self.assertIn('unbalanced_transaction',[i['code'] for i in report['issues']])

if __name__ == '__main__': unittest.main()
