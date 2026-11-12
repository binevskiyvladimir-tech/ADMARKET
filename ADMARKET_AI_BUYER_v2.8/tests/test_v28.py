import os, re, tempfile, unittest
from pathlib import Path
from fastapi.testclient import TestClient
from app import db
import app.main as main
from app.auth import hash_password

class PartnerRewardsV28Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        db.DATA = Path(self.tmp.name)
        db.DB_PATH = db.DATA / 'test.db'
        db.init_db()
        with db.connect() as con:
            self.supplier = con.execute("INSERT INTO users(email,password_hash,role,name,company,email_verified,referral_code) VALUES(?,?,?,?,?,1,?)",('s@example.test',hash_password('StrongPassword123'),'supplier','Supplier','S','ref-s')).lastrowid
            self.advertiser = con.execute("INSERT INTO users(email,password_hash,role,name,company,email_verified,referred_by_supplier_id) VALUES(?,?,?,?,?,1,?)",('a@example.test',hash_password('StrongPassword123'),'advertiser','Advertiser','A',self.supplier)).lastrowid
            con.execute("INSERT INTO partner_promotion_sites(supplier_id,publication_url,status) VALUES(?,?,'approved')",(self.supplier,'https://publisher.example.test/admarket'))
            con.execute("INSERT INTO partner_referral_events(supplier_id,event_type,referred_user_id) VALUES(?,'registration',?)",(self.supplier,self.advertiser))
            con.execute("INSERT INTO app_settings(key,value) VALUES('partner_reward_amount','250') ON CONFLICT(key) DO UPDATE SET value='250'")
            self.request = con.execute("INSERT INTO advertiser_requests(advertiser_id,title,goal,geo,budget) VALUES(?,?,?,?,?)",(self.advertiser,'Test deal','Awareness','RU',10000)).lastrowid
            self.response = con.execute("INSERT INTO request_responses(request_id,supplier_id,proposed_price,status) VALUES(?,?,?,'accepted')",(self.request,self.supplier,1000)).lastrowid
            self.placement = con.execute("INSERT INTO placements(response_id,status) VALUES(?,'completed')",(self.response,)).lastrowid
            self.terms = con.execute("INSERT INTO placement_terms(placement_id,version,deliverables,price,proposed_by,status) VALUES(?,1,'Post',1000,?,'accepted')",(self.placement,self.supplier)).lastrowid
            self.payment = con.execute("INSERT INTO payment_intents(placement_id,terms_id,advertiser_id,supplier_id,amount,commission_rate,commission_amount,supplier_amount,status,provider,mode,provider_reference,escrow_status) VALUES(?,?,?,?,?,12,120,880,'succeeded','sandbox','test','test-payment-0001','released')",(self.placement,self.terms,self.advertiser,self.supplier,1000)).lastrowid
            con.commit()

    def tearDown(self):
        self.tmp.cleanup()

    def test_qualified_sandbox_deal_accrues_once(self):
        with db.connect() as con:
            first = main.accrue_partner_reward(con,self.payment)
            second = main.accrue_partner_reward(con,self.payment)
            self.assertEqual(first,second)
            rows=con.execute('SELECT * FROM partner_rewards').fetchall()
            self.assertEqual(len(rows),1)
            self.assertEqual(rows[0]['amount'],250)
            self.assertEqual(rows[0]['status'],'pending')

    def test_default_zero_disables_reward(self):
        with db.connect() as con:
            con.execute("UPDATE app_settings SET value='0' WHERE key='partner_reward_amount'")
            self.assertIsNone(main.accrue_partner_reward(con,self.payment))
            self.assertEqual(con.execute('SELECT COUNT(*) FROM partner_rewards').fetchone()[0],0)

    def test_unapproved_partner_cannot_earn(self):
        with db.connect() as con:
            con.execute("UPDATE partner_promotion_sites SET status='pending' WHERE supplier_id=?",(self.supplier,))
            self.assertIsNone(main.accrue_partner_reward(con,self.payment))
            self.assertEqual(con.execute('SELECT COUNT(*) FROM partner_rewards').fetchone()[0],0)

    def test_only_released_sandbox_payment_qualifies(self):
        with db.connect() as con:
            con.execute("UPDATE payment_intents SET escrow_status='held' WHERE id=?",(self.payment,))
            self.assertIsNone(main.accrue_partner_reward(con,self.payment))
            con.execute("UPDATE payment_intents SET escrow_status='released',provider='live' WHERE id=?",(self.payment,))
            self.assertIsNone(main.accrue_partner_reward(con,self.payment))

    def test_admin_policy_and_reward_state_workflow(self):
        with db.connect() as con:
            admin_id=con.execute("INSERT INTO users(email,password_hash,role,name,company,email_verified) VALUES(?,?,?,?,?,1)",('admin@example.com',hash_password('AdminStrongPassword123'),'admin','Admin','ADMARKET')).lastrowid
            reward=con.execute("INSERT INTO partner_rewards(payment_intent_id,placement_id,advertiser_id,supplier_id,amount) VALUES(?,?,?,?,?)",(self.payment,self.placement,self.advertiser,self.supplier,250)).lastrowid
            con.commit()
        with TestClient(main.app) as client:
            login_html=client.get('/login').text
            csrf=re.search(r'name="csrf" value="([^"]+)',login_html).group(1)
            response=client.post('/login',data={'csrf':csrf,'email':'admin@example.com','password':'AdminStrongPassword123'},follow_redirects=False)
            self.assertEqual(response.status_code,303)
            admin_html=client.get('/admin').text
            self.assertIn('Партнёрские начисления · sandbox',admin_html)
            csrf=re.search(r'name="csrf" value="([^"]+)',admin_html).group(1)
            response=client.post('/admin/partner-rewards/policy',data={'csrf':csrf,'amount':'500'},follow_redirects=False)
            self.assertEqual(response.status_code,303)
            response=client.post(f'/admin/partner-rewards/{reward}/decision',data={'csrf':csrf,'status':'approved','note':'Проверено'},follow_redirects=False)
            self.assertEqual(response.status_code,303)
            response=client.post(f'/admin/partner-rewards/{reward}/sandbox-paid',data={'csrf':csrf,'note':'Тестовая отметка'},follow_redirects=False)
            self.assertEqual(response.status_code,303)
        with db.connect() as con:
            self.assertEqual(con.execute("SELECT value FROM app_settings WHERE key='partner_reward_amount'").fetchone()['value'],'500')
            self.assertEqual(con.execute("SELECT status FROM partner_rewards WHERE id=?",(reward,)).fetchone()['status'],'paid_sandbox')

    def test_migration_is_idempotent(self):
        db.init_db()
        db.init_db()
        with db.connect() as con:
            self.assertIn('partner_rewards', {r['name'] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")})
            self.assertEqual(con.execute("SELECT value FROM app_settings WHERE key='partner_reward_amount'").fetchone()['value'], '250')

    def test_multiple_rewards_raise_manual_review_flag(self):
        with db.connect() as con:
            main.accrue_partner_reward(con,self.payment)
            for n in range(2,4):
                req=con.execute("INSERT INTO advertiser_requests(advertiser_id,title,goal,geo,budget) VALUES(?,?,?,?,?)",(self.advertiser,f'Deal {n}','Awareness','RU',10000)).lastrowid
                resp=con.execute("INSERT INTO request_responses(request_id,supplier_id,proposed_price,status) VALUES(?,?,?,'accepted')",(req,self.supplier,1000)).lastrowid
                placement=con.execute("INSERT INTO placements(response_id,status) VALUES(?,'completed')",(resp,)).lastrowid
                terms=con.execute("INSERT INTO placement_terms(placement_id,version,deliverables,price,proposed_by,status) VALUES(?,1,'Post',1000,?,'accepted')",(placement,self.supplier)).lastrowid
                pay=con.execute("INSERT INTO payment_intents(placement_id,terms_id,advertiser_id,supplier_id,amount,commission_rate,commission_amount,supplier_amount,status,provider,mode,provider_reference,escrow_status) VALUES(?,?,?,?,?,12,120,880,'succeeded','sandbox','test',?,'released')",(placement,terms,self.advertiser,self.supplier,1000,f'test-payment-{n:04d}')).lastrowid
                main.accrue_partner_reward(con,pay)
            self.assertEqual(con.execute("SELECT COUNT(*) FROM partner_reward_flags WHERE status='open'").fetchone()[0],1)

if __name__ == '__main__': unittest.main()
