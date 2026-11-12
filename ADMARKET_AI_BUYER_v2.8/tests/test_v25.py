from pathlib import Path
import unittest
ROOT=Path(__file__).resolve().parents[1]
class IntegrationsV25Tests(unittest.TestCase):
 def test_additive_oauth_tables_and_owner_scoping(self):
  db=(ROOT/'app/db.py').read_text()
  for x in ('CREATE TABLE IF NOT EXISTS ad_platform_connections','CREATE TABLE IF NOT EXISTS ad_platform_oauth_states','REFERENCES users(id) ON DELETE CASCADE','UNIQUE(user_id,provider,external_account_id)'):
   self.assertIn(x,db)
  src=(ROOT/'app/main.py').read_text()
  for x in ('require_user(request, "advertiser")','state_hash','token_ciphertext','Fernet','/integrations/{connection_id}/disconnect'):
   self.assertIn(x,src)
 def test_provider_status_is_honest_about_disabled_sync(self):
  src=(ROOT/'app/main.py').read_text()
  self.assertIn('"sync_enabled":False',src)
  self.assertIn('authorized_pending_access',src)
  self.assertIn('Официальный flow и права доступа требуют отдельной проверки',src)
 def test_integration_page_discloses_scope(self):
  page=(ROOT/'app/templates/integrations.html').read_text()
  for term in ('OAuth завершён','синхронизация','VK Реклама','Токен хранится зашифрованным'):
   self.assertIn(term,page)
 def test_encryption_key_is_environment_only(self):
  env=(ROOT/'.env.example').read_text(); self.assertIn('INTEGRATION_ENCRYPTION_KEY=',env)
  self.assertIn('YANDEX_DIRECT_CLIENT_SECRET=',env)
if __name__=='__main__': unittest.main()
