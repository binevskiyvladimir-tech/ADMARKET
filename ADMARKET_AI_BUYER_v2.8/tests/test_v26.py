import os, re, tempfile, unittest
from pathlib import Path
from fastapi.testclient import TestClient
from app import db
import app.main as main
from app.main import app

class PartnerPromotionV26Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        db.DATA = Path(self.tmp.name)
        db.DB_PATH = db.DATA / 'test.db'
        os.environ['ADMIN_EMAIL'] = 'admin@example.com'
        os.environ['ADMIN_PASSWORD'] = 'TestAdminPassword123!'
        self.sent = []
        self.old_sender = main.send_account_email
        main.send_account_email = lambda to, subject, link: (self.sent.append((to, subject, link)) or True)

    def tearDown(self):
        main.send_account_email = self.old_sender
        os.environ.pop('ADMIN_PASSWORD', None)
        os.environ.pop('ADMIN_EMAIL', None)
        self.tmp.cleanup()

    @staticmethod
    def csrf(client, path='/register'):
        html = client.get(path).text
        m = re.search(r'name="csrf" value="([^"]+)', html)
        assert m, html[:500]
        return m.group(1), html

    def test_supplier_registration_review_gate_and_referral_attribution(self):
        with TestClient(app) as supplier_client:
            csrf, html = self.csrf(supplier_client)
            self.assertIn('supplier-promotion-field', html)
            registered = supplier_client.post('/register', data={
                'csrf': csrf, 'email': 'publisher@example.test', 'password': 'StrongPassword123',
                'role': 'supplier', 'name': 'Publisher', 'company': 'Publisher Co',
                'promotion_url': 'https://publisher.example.test/posts/admarket'
            }, follow_redirects=False)
            self.assertEqual(registered.status_code, 303)
            token = self.sent[-1][2].split('token=')[1]
            self.assertEqual(supplier_client.get('/verify-email', params={'token': token}).status_code, 200)
            csrf, _ = self.csrf(supplier_client, '/login')
            self.assertEqual(supplier_client.post('/login', data={'csrf': csrf, 'email': 'publisher@example.test', 'password': 'StrongPassword123'}, follow_redirects=False).status_code, 303)
            dashboard = supplier_client.get('/dashboard').text
            self.assertIn('Partner Promotion', dashboard)
            self.assertIn('Ожидает проверки', dashboard)
            with db.connect() as con:
                supplier = con.execute("SELECT id,referral_code FROM users WHERE email='publisher@example.test'").fetchone()
                site = con.execute("SELECT status,publication_url FROM partner_promotion_sites WHERE supplier_id=?", (supplier['id'],)).fetchone()
                self.assertEqual(site['status'], 'pending')
                self.assertEqual(site['publication_url'], 'https://publisher.example.test/posts/admarket')
                referral_code = supplier['referral_code']
            csrf = re.search(r'name="csrf" value="([^"]+)', dashboard).group(1)
            blocked = supplier_client.post('/supplier/offers', data={'csrf':csrf,'name':'Channel','channel':'Telegram','topic':'Tech','geo':'RU','price':'1000','audience':'10000','formats':'Post'}, follow_redirects=False)
            self.assertEqual(blocked.status_code, 403)

        # A separate browser session visits the partner URL and registers an advertiser.
        with TestClient(app) as referral_client:
            visit = referral_client.get(f'/r/{referral_code}', follow_redirects=False)
            self.assertEqual(visit.status_code, 302)
            self.assertEqual(visit.headers['location'], '/register')
            referral_client.get(f'/r/{referral_code}', follow_redirects=False)
            csrf, _ = self.csrf(referral_client)
            created = referral_client.post('/register', data={
                'csrf':csrf, 'email':'referred-buyer@example.test', 'password':'StrongPassword123',
                'role':'advertiser', 'name':'Referred Buyer', 'company':'Buyer Co'
            }, follow_redirects=False)
            self.assertEqual(created.status_code, 303)
            with db.connect() as con:
                supplier = con.execute("SELECT id FROM users WHERE email='publisher@example.test'").fetchone()
                buyer = con.execute("SELECT referred_by_supplier_id FROM users WHERE email='referred-buyer@example.test'").fetchone()
                self.assertEqual(buyer['referred_by_supplier_id'], supplier['id'])
                clicks = con.execute("SELECT COUNT(*) FROM partner_referral_events WHERE supplier_id=? AND event_type='click'",(supplier['id'],)).fetchone()[0]
                registrations = con.execute("SELECT COUNT(*) FROM partner_referral_events WHERE supplier_id=? AND event_type='registration'",(supplier['id'],)).fetchone()[0]
                self.assertEqual(clicks, 1)
                self.assertEqual(registrations, 1)

        # Only the allowlisted admin can approve; approval opens supplier offer publishing.
        with TestClient(app) as admin_client:
            csrf, _ = self.csrf(admin_client, '/login')
            admin_login = admin_client.post('/login', data={'csrf':csrf,'email':'admin@example.com','password':'TestAdminPassword123!'}, follow_redirects=False)
            self.assertEqual(admin_login.status_code, 303)
            admin_html = admin_client.get('/admin').text
            self.assertIn('Partner Promotion', admin_html)
            csrf = re.search(r'name="csrf" value="([^"]+)', admin_html).group(1)
            with db.connect() as con:
                site_id = con.execute("SELECT id FROM partner_promotion_sites").fetchone()['id']
            approved = admin_client.post(f'/admin/partner-promotion/{site_id}/review', data={'csrf':csrf,'status':'approved','note':'Ссылка на ADMARKET присутствует'}, follow_redirects=False)
            self.assertEqual(approved.status_code, 303)

        with TestClient(app) as supplier_client:
            csrf, _ = self.csrf(supplier_client, '/login')
            supplier_client.post('/login', data={'csrf':csrf,'email':'publisher@example.test','password':'StrongPassword123'}, follow_redirects=False)
            dashboard = supplier_client.get('/dashboard').text
            self.assertIn('Привлечённые рекламодатели', dashboard)
            csrf = re.search(r'name="csrf" value="([^"]+)', dashboard).group(1)
            allowed = supplier_client.post('/supplier/offers', data={'csrf':csrf,'name':'Channel','channel':'Telegram','topic':'Tech','geo':'RU','price':'1000','audience':'10000','formats':'Post'}, follow_redirects=False)
            self.assertEqual(allowed.status_code, 303)

    def test_supplier_registration_rejects_non_https_publication(self):
        with TestClient(app) as client:
            csrf, _ = self.csrf(client)
            response = client.post('/register', data={
                'csrf':csrf, 'email':'bad-publisher@example.test', 'password':'StrongPassword123',
                'role':'supplier', 'name':'Bad Publisher', 'promotion_url':'http://example.test/post'
            })
            self.assertEqual(response.status_code, 400)

if __name__ == '__main__':
    unittest.main()
