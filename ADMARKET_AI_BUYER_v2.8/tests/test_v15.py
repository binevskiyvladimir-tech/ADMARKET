import tempfile
import unittest
from pathlib import Path
from app import db
from app.main import supplier_quality, scan_fraud_signals

class V15QualityTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); db.DATA=Path(self.tmp.name); db.DB_PATH=db.DATA/'test.db'; db.init_db()
        with db.connect() as con:
            con.execute("INSERT INTO users(email,password_hash,role,supplier_verification) VALUES('buyer@test.local','x','advertiser','unverified')")
            con.execute("INSERT INTO users(email,password_hash,role,supplier_verification) VALUES('seller1@test.local','x','supplier','verified')")
            con.execute("INSERT INTO users(email,password_hash,role) VALUES('seller2@test.local','x','supplier')")
            con.execute("INSERT INTO supplier_offers(supplier_id,name,channel,topic,geo,audience,price,formats,details) VALUES(2,'Channel A','Telegram','Tech','RU',10000,5000,'Post','')")
            con.execute("INSERT INTO supplier_offers(supplier_id,name,channel,topic,geo,audience,price,formats,details) VALUES(3,'Channel A','Telegram','Tech','RU',10000,5000,'Post','')")
            con.commit()
    def tearDown(self): self.tmp.cleanup()
    def test_migration_idempotent_and_quality_score_explainable(self):
        db.init_db()
        with db.connect() as con:
            q=supplier_quality(con,2)
            self.assertEqual(q['supplier_id'],2)
            self.assertGreaterEqual(q['score'],0); self.assertLessEqual(q['score'],100)
            self.assertEqual(q['verification_score'],100)
            self.assertEqual(con.execute('SELECT COUNT(*) FROM supplier_quality_snapshots').fetchone()[0],0)
    def test_duplicate_offer_scan_is_deduplicated_and_review_only(self):
        with db.connect() as con:
            self.assertEqual(scan_fraud_signals(con),2)
            self.assertEqual(scan_fraud_signals(con),0)
            rows=con.execute('SELECT * FROM fraud_review_signals ORDER BY id').fetchall()
            self.assertEqual(len(rows),2)
            self.assertTrue(all(r['status']=='open' for r in rows))
            self.assertTrue(all(r['signal_type']=='cross_supplier_duplicate' for r in rows))
    def test_quality_score_never_treats_dismissed_complaint_as_proven(self):
        with db.connect() as con:
            con.execute("INSERT INTO advertiser_requests(advertiser_id,title,goal,geo,budget) VALUES(1,'Campaign','Leads','RU',5000)")
            con.execute("INSERT INTO request_responses(request_id,supplier_id,proposed_price,status) VALUES(1,2,1000,'accepted')")
            con.execute("INSERT INTO placements(response_id,status) VALUES(1,'completed')")
            con.execute("INSERT INTO supplier_complaints(placement_id,opened_by,supplier_id,category,description,status) VALUES(1,1,2,'quality','This complaint was reviewed and not substantiated.','dismissed')")
            con.commit()
            q=supplier_quality(con,2)
            self.assertEqual(q['active_complaints'],0)
            self.assertEqual(q['complaint_score'],100)

if __name__=='__main__': unittest.main()
