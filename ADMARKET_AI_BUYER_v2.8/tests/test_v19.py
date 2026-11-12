from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]

class ResponsiveWorkspaceTests(unittest.TestCase):
    def test_desktop_styles_and_breakpoints_exist(self):
        css = (ROOT / "app/static/style.css").read_text()
        self.assertIn(".hero-layout", css)
        self.assertIn("@media(min-width:1200px)", css)
        self.assertIn("@media(max-width:760px)", css)
        self.assertIn("prefers-reduced-motion", css)

    def test_homepage_has_desktop_preview_and_mobile_viewport(self):
        home = (ROOT / "app/templates/index.html").read_text()
        base = (ROOT / "app/templates/base.html").read_text()
        self.assertIn("hero-preview", home)
        self.assertIn('name="viewport"', base)

    def test_admin_email_gate_remains_in_navigation(self):
        base = (ROOT / "app/templates/base.html").read_text()
        self.assertIn("user.role == 'admin'", base)

    def test_v19_design_system_and_workspace_navigation(self):
        css = (ROOT / "app/static/style.css").read_text()
        base = (ROOT / "app/templates/base.html").read_text()
        self.assertIn("--lime:#c2f36b", css)
        self.assertIn(".quick-actions", css)
        self.assertIn(".kpi-grid", css)
        self.assertIn("/analytics", base)
        self.assertIn("SANDBOX MODE", base)
        home = (ROOT / "app/templates/index.html").read_text()
        self.assertIn("compare-panel", home)
        self.assertIn("max-price", home)
        self.assertIn("min-audience", home)

    def test_analytics_keeps_server_values_and_sandbox_disclaimer(self):
        analytics = (ROOT / "app/templates/analytics.html").read_text()
        for key in ("requests_count", "total", "completed", "avg_rating", "paid", "held", "rows"):
            self.assertIn(key, analytics)
        self.assertIn("только тестовые проводки", analytics)

if __name__ == "__main__":
    unittest.main()
