from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]

class WorkspaceV20Tests(unittest.TestCase):
    def test_workspace_summary_uses_existing_role_scoped_records(self):
        source = (ROOT / "app/main.py").read_text()
        for fragment in (
            "workspace_stats = {}", "workspace_actions = []",
            "ar.advertiser_id=? AND rr.status='pending'",
            "rr.supplier_id=? AND p.status IN ('awaiting_supplier','confirmed','published')",
            "SELECT COUNT(*) FROM notifications WHERE user_id=? AND is_read=0",
        ):
            self.assertIn(fragment, source)

    def test_dashboard_renders_summary_and_role_actions(self):
        dashboard = (ROOT / "app/templates/dashboard.html").read_text()
        for fragment in ("workspace-overview", "workspace-kpis", "workspace-actions", 'id="opportunities"', 'id="responses"', 'id="placements"'):
            self.assertIn(fragment, dashboard)

    def test_no_schema_migration_added_for_workspace_overview(self):
        db = (ROOT / "app/db.py").read_text()
        self.assertNotIn("v2.0", db)
        self.assertIn("CREATE TABLE IF NOT EXISTS advertiser_requests", db)
        self.assertIn("CREATE TABLE IF NOT EXISTS placements", db)

    def test_version_and_admin_gate_are_preserved(self):
        source = (ROOT / "app/main.py").read_text()
        auth = (ROOT / "app/auth.py").read_text()
        self.assertIn('version="2.8.0"', source)
        self.assertIn('admin@example.com', auth)
        self.assertIn('if not is_authorized_admin(user)', source)

if __name__ == "__main__":
    unittest.main()
