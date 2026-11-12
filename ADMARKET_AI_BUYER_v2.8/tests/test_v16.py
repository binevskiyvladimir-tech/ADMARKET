import tempfile, unittest
from pathlib import Path
from app import db
from app.platform_trust import assess_account

class PlatformTrustTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); db.DATA=Path(self.tmp.name); db.DB_PATH=db.DATA/'test.db'; db.init_db()
        with db.connect() as con:
            con.execute("INSERT INTO users(email,password_hash,role) VALUES('supplier@test.local','x','supplier')")
            con.execute("INSERT INTO platform_accounts(supplier_id,platform,handle,profile_url) VALUES(1,'VK','club1','https://vk.com/club1')")
            con.commit()
    def tearDown(self): self.tmp.cleanup()
    def test_single_snapshot_is_insufficient_not_fraud(self):
        with db.connect() as con:
            cur=con.execute("INSERT INTO platform_metric_snapshots(account_id,followers,avg_views,avg_reach,engagement_rate) VALUES(1,10000,5000,7000,4.5)")
            q=assess_account(con,1); con.commit()
            self.assertEqual(q['confidence'],'insufficient'); self.assertEqual(q['trust_score'],100)
            self.assertEqual(con.execute('SELECT COUNT(*) FROM platform_trust_signals').fetchone()[0],0)
    def test_growth_anomaly_creates_deduplicated_human_review_signal(self):
        with db.connect() as con:
            con.execute("INSERT INTO platform_metric_snapshots(account_id,followers,avg_views,avg_reach,engagement_rate,sampled_at) VALUES(1,10000,5000,7000,4.5,'2026-11-01 10:00:00')")
            con.execute("INSERT INTO platform_metric_snapshots(account_id,followers,avg_views,avg_reach,engagement_rate,sampled_at) VALUES(1,20000,6000,9000,5.0,'2026-11-10 10:00:00')")
            q=assess_account(con,1); con.commit()
            self.assertEqual(q['confidence'],'medium'); self.assertEqual(q['growth_pct'],100.0)
            self.assertTrue(any('Рост подписчиков' in f for f in q['flags']))
            self.assertEqual(con.execute("SELECT COUNT(*) FROM platform_trust_signals WHERE status='open'").fetchone()[0],1)
            assess_account(con,1); con.commit()
            self.assertEqual(con.execute('SELECT COUNT(*) FROM platform_trust_signals').fetchone()[0],1)
    def test_metric_history_is_immutable(self):
        with db.connect() as con:
            cur=con.execute("INSERT INTO platform_metric_snapshots(account_id,followers,avg_views,avg_reach,engagement_rate) VALUES(1,100,50,60,2)")
            with self.assertRaises(Exception): con.execute("UPDATE platform_metric_snapshots SET followers=200 WHERE id=?",(cur.lastrowid,))
    def test_reject_invalid_metric_ranges(self):
        with db.connect() as con:
            with self.assertRaises(Exception): con.execute("INSERT INTO platform_metric_snapshots(account_id,followers,avg_views,avg_reach,engagement_rate) VALUES(1,-1,50,60,2)")

if __name__=='__main__': unittest.main()
