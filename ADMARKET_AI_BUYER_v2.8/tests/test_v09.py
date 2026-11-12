import tempfile
import unittest
from pathlib import Path
from app import db

class V09MigrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        db.DATA = Path(self.tmp.name)
        db.DB_PATH = db.DATA / 'test.db'
        db.init_db()

    def tearDown(self):
        self.tmp.cleanup()

    def test_migration_is_idempotent_and_preserves_core_tables(self):
        db.init_db()
        with db.connect() as con:
            tables = {r['name'] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            for name in ('users','inventory','orders','placements','placement_terms','financial_estimates',
                         'account_tokens','payment_intents','ledger_transactions','ledger_entries','refunds',
                         'reviews','risk_events','supplier_verification_events'):
                self.assertIn(name, tables)
            cols = {r[1] for r in con.execute('PRAGMA table_info(users)')}
            self.assertIn('supplier_verification', cols)
            self.assertIn('risk_level', cols)

    def test_balanced_sandbox_posting_and_full_reversal(self):
        with db.connect() as con:
            con.execute("INSERT INTO users(email,password_hash,role) VALUES('a@x.test','x','advertiser')")
            con.execute("INSERT INTO users(email,password_hash,role) VALUES('s@x.test','x','supplier')")
            aid, sid = 1, 2
            con.execute("INSERT INTO advertiser_requests(advertiser_id,title,goal,geo,budget) VALUES(1,'Test','Leads','RU',10000)")
            con.execute("INSERT INTO request_responses(request_id,supplier_id,proposed_price) VALUES(1,2,1000)")
            con.execute("INSERT INTO placements(response_id,status) VALUES(1,'completed')")
            con.execute("INSERT INTO placement_terms(placement_id,version,deliverables,price,proposed_by,status) VALUES(1,1,'Post',1000,2,'accepted')")
            con.execute("INSERT INTO payment_intents(placement_id,terms_id,advertiser_id,supplier_id,amount,commission_rate,commission_amount,supplier_amount,provider_reference,status) VALUES(1,1,1,2,1000,12,120,880,'test_ref','succeeded')")
            tx = con.execute("INSERT INTO ledger_transactions(payment_intent_id,kind,description) VALUES(1,'sandbox_payment','test')").lastrowid
            entries=[('user',1,-1000),('user',2,880),('platform',None,120)]
            con.executemany('INSERT INTO ledger_entries(transaction_id,account_type,user_id,amount) VALUES(?,?,?,?)',[(tx,*e) for e in entries])
            self.assertEqual(con.execute('SELECT SUM(amount) FROM ledger_entries WHERE transaction_id=?',(tx,)).fetchone()[0],0)
            refund_tx=con.execute("INSERT INTO ledger_transactions(refund_of,kind,description) VALUES(?,'refund','test reversal')",(tx,)).lastrowid
            original=con.execute('SELECT account_type,user_id,amount FROM ledger_entries WHERE transaction_id=?',(tx,)).fetchall()
            con.executemany('INSERT INTO ledger_entries(transaction_id,account_type,user_id,amount) VALUES(?,?,?,?)',[(refund_tx,e['account_type'],e['user_id'],-e['amount']) for e in original])
            self.assertEqual(con.execute('SELECT SUM(amount) FROM ledger_entries WHERE transaction_id=?',(refund_tx,)).fetchone()[0],0)
            self.assertEqual(con.execute('SELECT SUM(amount) FROM ledger_entries WHERE account_type="user" AND user_id=1').fetchone()[0],0)
            self.assertEqual(con.execute('SELECT SUM(amount) FROM ledger_entries WHERE account_type="user" AND user_id=2').fetchone()[0],0)
            self.assertEqual(con.execute('SELECT SUM(amount) FROM ledger_entries WHERE account_type="platform"').fetchone()[0],0)
            con.commit()

    def test_review_and_verification_constraints(self):
        with db.connect() as con:
            con.execute("INSERT INTO users(email,password_hash,role) VALUES('s@x.test','x','supplier')")
            con.execute("INSERT INTO users(email,password_hash,role) VALUES('a@x.test','x','advertiser')")
            con.execute("INSERT INTO advertiser_requests(advertiser_id,title,goal,geo,budget) VALUES(2,'Test','Leads','RU',1000)")
            con.execute("INSERT INTO request_responses(request_id,supplier_id,proposed_price,status) VALUES(1,1,500,'accepted')")
            con.execute("INSERT INTO placements(response_id,status) VALUES(1,'completed')")
            con.execute("INSERT INTO reviews(placement_id,reviewer_id,reviewee_id,rating,body) VALUES(1,2,1,5,'Good')")
            with self.assertRaises(Exception):
                con.execute("INSERT INTO reviews(placement_id,reviewer_id,reviewee_id,rating,body) VALUES(1,2,1,5,'Duplicate')")
            con.execute("UPDATE users SET supplier_verification='verified' WHERE id=1")
            self.assertEqual(con.execute('SELECT supplier_verification FROM users WHERE id=1').fetchone()[0],'verified')

if __name__ == '__main__': unittest.main()
