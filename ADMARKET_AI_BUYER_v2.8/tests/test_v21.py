from pathlib import Path
import unittest
ROOT = Path(__file__).resolve().parents[1]
class CampaignV21Tests(unittest.TestCase):
    def test_campaign_schema_and_indexes_are_additive(self):
        db=(ROOT/'app/db.py').read_text()
        self.assertIn('CREATE TABLE IF NOT EXISTS campaigns',db)
        self.assertIn('CREATE TABLE IF NOT EXISTS campaign_events',db)
        self.assertIn('REFERENCES advertiser_requests(id) ON DELETE SET NULL',db)
    def test_campaign_routes_are_role_and_owner_scoped(self):
        src=(ROOT/'app/main.py').read_text()
        self.assertIn('require_user(request, "advertiser")',src)
        self.assertIn('WHERE id=? AND advertiser_id=?',src)
        self.assertIn('campaign_events(campaign_id,actor_id,event_type,details)',src)
        self.assertIn('version":"2.8.0"',src)
    def test_dashboard_campaign_controls_are_present(self):
        page=(ROOT/'app/templates/dashboard.html').read_text()
        for term in ('id="campaigns"','/advertiser/campaigns','campaign-status-form','Создать черновик кампании'):
            self.assertIn(term,page)
    def test_admin_gate_is_unchanged(self):
        auth=(ROOT/'app/auth.py').read_text()
        self.assertIn('admin@example.com',auth)
        self.assertIn("role", auth)
if __name__=='__main__': unittest.main()
