from pathlib import Path
import unittest
ROOT=Path(__file__).resolve().parents[1]
class CampaignAnalyticsV23Tests(unittest.TestCase):
 def test_additive_metric_snapshot_schema(self):
  s=(ROOT/'app/db.py').read_text(); self.assertIn('CREATE TABLE IF NOT EXISTS campaign_metric_snapshots',s); self.assertIn('UNIQUE(campaign_id, metric_date)',s); self.assertIn("CHECK(source IN ('manual','import'))",s)
 def test_owner_scoped_routes_and_kpi_formulas(self):
  s=(ROOT/'app/main.py').read_text(); self.assertIn('WHERE id=? AND advertiser_id=?',s); self.assertIn('require_user(request,"advertiser")',s); self.assertIn('campaign_metric_snapshots',s); self.assertIn('t["clicks"]/t["impressions"]*100',s); self.assertIn('t["revenue"]-t["spend"]',s)
 def test_validation_export_and_page(self):
  s=(ROOT/'app/main.py').read_text(); self.assertIn('datetime.strptime(metric_date,"%Y-%m-%d")',s); self.assertIn('clicks>impressions',s); self.assertIn('StreamingResponse',s)
  t=(ROOT/'app/templates/campaign_analytics.html').read_text(); self.assertIn('DAILY SNAPSHOTS',t); self.assertIn('Экспорт CSV',t); self.assertIn('не проверяет цифры',t)
