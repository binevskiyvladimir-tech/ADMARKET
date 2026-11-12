import tempfile
import unittest
from pathlib import Path
from app import db

class V13DeliveryReceiptTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        db.DATA=Path(self.tmp.name); db.DB_PATH=db.DATA/'test.db'
        db.init_db()
        with db.connect() as con:
            con.execute("INSERT INTO users(email,password_hash,role) VALUES('buyer@v13.test','x','advertiser')")
            con.execute("INSERT INTO users(email,password_hash,role) VALUES('seller@v13.test','x','supplier')")
            con.execute("INSERT INTO advertiser_requests(advertiser_id,title,goal,geo,budget) VALUES(1,'Campaign','Leads','RU',5000)")
            con.execute("INSERT INTO request_responses(request_id,supplier_id,proposed_price,status) VALUES(1,2,1000,'accepted')")
            con.execute("INSERT INTO placements(response_id,status,publication_url) VALUES(1,'published','https://example.com/post')")
            con.commit()
    def tearDown(self): self.tmp.cleanup()
    def test_migration_is_idempotent_and_preserves_placements(self):
        db.init_db()
        with db.connect() as con:
            self.assertEqual(con.execute('SELECT COUNT(*) FROM placements').fetchone()[0],1)
            self.assertEqual(con.execute('SELECT COUNT(*) FROM placement_delivery_receipts').fetchone()[0],0)
            con.execute("INSERT INTO placement_delivery_receipts(placement_id,supplier_id,publication_url) VALUES(1,2,'https://example.com/post')")
            with self.assertRaises(Exception):
                con.execute("INSERT INTO placement_delivery_receipts(placement_id,supplier_id,publication_url) VALUES(1,2,'https://example.com/duplicate')")
    def test_acceptance_snapshot_is_immutable_record(self):
        with db.connect() as con:
            con.execute("INSERT INTO placement_delivery_receipts(placement_id,supplier_id,publication_url) VALUES(1,2,'https://example.com/post')")
            con.execute("UPDATE placement_delivery_receipts SET status='accepted',accepted_by=1,accepted_at=CURRENT_TIMESTAMP WHERE placement_id=1")
            row=con.execute('SELECT * FROM placement_delivery_receipts WHERE placement_id=1').fetchone()
            self.assertEqual(row['status'],'accepted'); self.assertEqual(row['accepted_by'],1)
            self.assertEqual(row['publication_url'],'https://example.com/post')
            with self.assertRaises(Exception):
                con.execute("UPDATE placement_delivery_receipts SET publication_url='https://example.com/edited' WHERE placement_id=1")

if __name__=='__main__': unittest.main()
