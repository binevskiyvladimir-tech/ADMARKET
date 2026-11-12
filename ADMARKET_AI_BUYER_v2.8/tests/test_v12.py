import tempfile
import unittest
from pathlib import Path
from app import db

class V12DisputeMigrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        db.DATA=Path(self.tmp.name); db.DB_PATH=db.DATA/'test.db'
        db.init_db()
        with db.connect() as con:
            con.execute("INSERT INTO users(email,password_hash,role) VALUES('buyer@v12.test','x','advertiser')")
            con.execute("INSERT INTO users(email,password_hash,role) VALUES('seller@v12.test','x','supplier')")
            con.execute("INSERT INTO advertiser_requests(advertiser_id,title,goal,geo,budget) VALUES(1,'Campaign','Leads','RU',5000)")
            con.execute("INSERT INTO request_responses(request_id,supplier_id,proposed_price,status) VALUES(1,2,1000,'accepted')")
            con.execute("INSERT INTO placements(response_id,status) VALUES(1,'completed')")
            con.commit()

    def tearDown(self): self.tmp.cleanup()

    def test_migration_is_idempotent_and_preserves_existing_rows(self):
        db.init_db()
        with db.connect() as con:
            self.assertEqual(con.execute('SELECT COUNT(*) FROM placements').fetchone()[0],1)
            self.assertEqual(con.execute("SELECT COUNT(*) FROM disputes").fetchone()[0],0)
            con.execute("INSERT INTO disputes(placement_id,opened_by,reason) VALUES(1,1,'This is a valid dispute reason.')")
            with self.assertRaises(Exception):
                con.execute("INSERT INTO disputes(placement_id,opened_by,reason) VALUES(1,2,'A second active dispute reason.')")

    def test_resolved_dispute_can_be_followed_by_new_dispute(self):
        with db.connect() as con:
            con.execute("INSERT INTO disputes(placement_id,opened_by,reason,status) VALUES(1,1,'This is a valid dispute reason.','resolved_for_advertiser')")
            con.execute("INSERT INTO disputes(placement_id,opened_by,reason) VALUES(1,2,'A later separate dispute reason.')")
            self.assertEqual(con.execute('SELECT COUNT(*) FROM disputes').fetchone()[0],2)

if __name__=='__main__': unittest.main()
