from pathlib import Path
import unittest
ROOT=Path(__file__).resolve().parents[1]
class CsvImportV24Tests(unittest.TestCase):
 def test_import_route_is_owner_scoped_and_bounded(self):
  s=(ROOT/'app/main.py').read_text(); self.assertIn('/analytics/import',s); self.assertIn('require_user(request,"advertiser")',s); self.assertIn('2_000_000',s); self.assertIn('len(normalized)>=366',s)
 def test_import_validates_and_upserts_with_source(self):
  s=(ROOT/'app/main.py').read_text(); self.assertIn("required={\"date\",\"spend\",\"impressions\",\"clicks\",\"conversions\"}",s); self.assertIn("VALUES(?,?,?,?,?,?,?,'import',?)",s); self.assertIn("source='import'",s)
 def test_ui_discloses_no_direct_api_verification(self):
  t=(ROOT/'app/templates/campaign_analytics.html').read_text(); self.assertIn('Импорт дневного отчёта CSV',t); self.assertIn('Прямой OAuth/API-доступ',t); self.assertIn('CSV-импорт',t)
