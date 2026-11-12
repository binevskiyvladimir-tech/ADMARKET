import tempfile
import unittest
from pathlib import Path
from app import db

class V14SupplierQualityTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); db.DATA=Path(self.tmp.name); db.DB_PATH=db.DATA/'test.db'; db.init_db()
        with db.connect() as con:
            con.execute("INSERT INTO users(email,password_hash,role) VALUES('buyer@v14.test','x','advertiser')")
            con.execute("INSERT INTO users(email,password_hash,role) VALUES('seller@v14.test','x','supplier')")
            con.execute("INSERT INTO advertiser_requests(advertiser_id,title,goal,geo,budget) VALUES(1,'Campaign','Leads','RU',5000)")
            con.execute("INSERT INTO request_responses(request_id,supplier_id,proposed_price,status) VALUES(1,2,1000,'accepted')")
            con.execute("INSERT INTO placements(response_id,status) VALUES(1,'confirmed')")
            con.commit()
    def tearDown(self): self.tmp.cleanup()
    def test_migration_idempotent_and_legacy_rows_preserved(self):
        db.init_db()
        with db.connect() as con:
            self.assertEqual(con.execute('SELECT COUNT(*) FROM placements').fetchone()[0],1)
            self.assertEqual(con.execute('SELECT COUNT(*) FROM supplier_sla_cases').fetchone()[0],0)
            self.assertEqual(con.execute('SELECT COUNT(*) FROM supplier_complaints').fetchone()[0],0)
    def test_sla_status_and_complaint_audit(self):
        with db.connect() as con:
            con.execute("INSERT INTO supplier_sla_cases(placement_id,supplier_id,due_at) VALUES(1,2,'2020-01-01 00:00:00')")
            con.execute("UPDATE supplier_sla_cases SET status='breached',breached_at=CURRENT_TIMESTAMP WHERE placement_id=1")
            cur=con.execute("INSERT INTO supplier_complaints(placement_id,opened_by,supplier_id,category,description) VALUES(1,1,2,'quality','Publication quality was materially below the agreed brief.')")
            con.execute("INSERT INTO supplier_complaint_events(complaint_id,actor_id,event_type,details) VALUES(?,?,?,?)",(cur.lastrowid,1,'opened','quality'))
            con.commit()
            self.assertEqual(con.execute("SELECT status FROM supplier_sla_cases WHERE placement_id=1").fetchone()[0],'breached')
            self.assertEqual(con.execute("SELECT COUNT(*) FROM supplier_complaint_events WHERE complaint_id=?",(cur.lastrowid,)).fetchone()[0],1)
    def test_invalid_complaint_category_rejected_by_constraint(self):
        with db.connect() as con:
            with self.assertRaises(Exception):
                con.execute("INSERT INTO supplier_complaints(placement_id,opened_by,supplier_id,category,description) VALUES(1,1,2,'fake','Long enough complaint description.')")

if __name__=='__main__': unittest.main()
