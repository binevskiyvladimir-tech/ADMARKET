import os, re, tempfile, unittest
from pathlib import Path
from fastapi.testclient import TestClient
from app import db
import app.main as main
from app.main import app

class PartnerAnalyticsV27Tests(unittest.TestCase):
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

    def create_account(self, client, email, role):
        html = client.get('/register').text
        csrf = re.search(r'name="csrf" value="([^"]+)', html).group(1)
        data = {'csrf': csrf, 'email': email, 'password': 'StrongPassword123', 'role': role,
                'name': 'Test User', 'company': 'Test Co'}
        if role == 'supplier': data['promotion_url'] = 'https://publisher.example.test/admarket'
        response = client.post('/register', data=data, follow_redirects=False)
        self.assertEqual(response.status_code, 303)
        token = self.sent[-1][2].split('token=')[1]
        self.assertEqual(client.get('/verify-email', params={'token': token}).status_code, 200)
        login_html = client.get('/login').text
        csrf = re.search(r'name="csrf" value="([^"]+)', login_html).group(1)
        response = client.post('/login', data={'csrf': csrf, 'email': email, 'password': 'StrongPassword123'}, follow_redirects=False)
        self.assertEqual(response.status_code, 303)

    def test_supplier_csv_export_is_owner_scoped_and_aggregated(self):
        with TestClient(app) as supplier:
            self.create_account(supplier, 'supplier@example.test', 'supplier')
            with db.connect() as con:
                sid = con.execute("SELECT id FROM users WHERE email='supplier@example.test'").fetchone()['id']
                con.execute("INSERT INTO partner_referral_events(supplier_id,event_type) VALUES(?,'click'),(?,'click'),(?,'registration')", (sid,sid,sid))
                from app.auth import hash_password
                other = con.execute("INSERT INTO users(email,password_hash,role,name,company,email_verified,referral_code) VALUES(?,?,?,?,?,1,?)", ('other-supplier@example.test',hash_password('StrongPassword123'),'supplier','Other','Other Co','otherrefcode')).lastrowid
                for _ in range(5): con.execute("INSERT INTO partner_referral_events(supplier_id,event_type) VALUES(?,'click')", (other,))
                con.commit()
            response = supplier.get('/supplier/partner-promotion/export.csv?period=30')
            self.assertEqual(response.status_code, 200)
            self.assertIn('text/csv', response.headers['content-type'])
            self.assertIn('referral_clicks', response.text)
            self.assertIn('2;1;50.0', response.text)
            self.assertIn('no-store', response.headers['cache-control'])
            self.assertEqual(supplier.get('/health').json()['version'], '2.8.0')
            self.assertEqual(supplier.get('/supplier/partner-promotion/export.csv?period=999').status_code, 400)

    def test_advertiser_cannot_access_supplier_partner_export(self):
        with TestClient(app) as advertiser:
            self.create_account(advertiser, 'buyer@example.test', 'advertiser')
            self.assertEqual(advertiser.get('/supplier/partner-promotion/export.csv').status_code, 403)

    def test_supplier_dashboard_shows_partner_analytics(self):
        with TestClient(app) as supplier:
            self.create_account(supplier, 'publisher@example.test', 'supplier')
            dashboard = supplier.get('/dashboard')
            self.assertEqual(dashboard.status_code, 200)
            self.assertIn('Динамика за последние 14 дней', dashboard.text)
            self.assertIn('Скачать отчёт за 30 дней', dashboard.text)

if __name__ == '__main__':
    unittest.main()
