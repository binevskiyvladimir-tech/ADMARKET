from pathlib import Path
import unittest, tempfile
from app import db
ROOT=Path(__file__).resolve().parents[1]
class MediaPlanningV22Tests(unittest.TestCase):
 def test_additive_saved_plan_schema(self):
  s=(ROOT/'app/db.py').read_text(); self.assertIn('CREATE TABLE IF NOT EXISTS media_plans',s); self.assertIn('REFERENCES campaigns(id) ON DELETE SET NULL',s)
 def test_routes_require_advertiser_and_owner_scope(self):
  s=(ROOT/'app/main.py').read_text(); self.assertIn('@app.get("/media-planner"',s); self.assertIn('WHERE id=? AND advertiser_id=?',s); self.assertIn('require_user(request, "advertiser")',s); self.assertIn('media_plan_attached',s)
 def test_planner_template_has_disclaimer_and_attach(self):
  s=(ROOT/'app/templates/media_planner.html').read_text(); self.assertIn('Цены, охваты и доступность не гарантированы',s); self.assertIn('/attach',s); self.assertIn('Рассчитать и сохранить',s)
 def test_migration_is_idempotent_and_preserves_campaigns(self):
  with tempfile.TemporaryDirectory() as tmp:
   old_data,old_path=db.DATA,db.DB_PATH
   try:
    db.DATA=Path(tmp); db.DB_PATH=db.DATA/'migration.db'; db.init_db()
    with db.connect() as con:
     uid=con.execute("INSERT INTO users(email,password_hash,role,name) VALUES('migrate@test.local','x','advertiser','Test')").lastrowid
     cid=con.execute("INSERT INTO campaigns(advertiser_id,title,budget) VALUES(?, 'Existing', 5000)",(uid,)).lastrowid
     con.commit()
    db.init_db()
    with db.connect() as con:
     self.assertIsNotNone(con.execute('SELECT id FROM campaigns WHERE id=?',(cid,)).fetchone())
     self.assertIsNotNone(con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='media_plans'").fetchone())
   finally: db.DATA,db.DB_PATH=old_data,old_path
 def test_existing_admin_gate_unchanged(self):
  s=(ROOT/'app/auth.py').read_text(); self.assertIn('admin@example.com',s)
 def test_version_and_api_key_not_bundled(self):
  s=(ROOT/'app/main.py').read_text(); self.assertIn('version="2.8.0"',s); env=(ROOT/'.env.example').read_text(); self.assertIn('OPENAI_API_KEY=',env)
if __name__=='__main__': unittest.main()
