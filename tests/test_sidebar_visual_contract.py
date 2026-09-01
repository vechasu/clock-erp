import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class SidebarVisualContractTest(unittest.TestCase):
    def source(self, relative_path):
        return (ROOT / relative_path).read_text(encoding="utf-8")

    def test_header_uses_existing_mark_and_line_icons_only(self):
        sidebar = self.source("app/templates/_sidebar.html")

        self.assertIn("img/tictactoy-logo.svg", sidebar)
        self.assertIn('class="sidebar-brand-actions"', sidebar)
        self.assertIn('class="notification-bell"', sidebar)
        self.assertIn('class="sidebar-toggle"', sidebar)
        self.assertNotIn('class="sidebar-brand-copy"', sidebar)
        self.assertNotIn('class="sidebar-brand-title"', sidebar)
        self.assertNotIn('class="sidebar-brand-subtitle"', sidebar)
        self.assertNotIn("🔔", sidebar)

        logo = self.source("app/static/img/tictactoy-logo.svg")
        self.assertIn('M0 0h3000v333', logo)
        self.assertIn('v667', logo)
        self.assertNotIn('transform=', logo)

    def test_existing_sidebar_behaviour_and_data_hooks_are_preserved(self):
        sidebar = self.source("app/templates/_sidebar.html")
        sidebar_script = self.source("app/static/js/sidebar.js")

        for marker in (
            "sidebar_navigation_items",
            "item.active",
            "item.badge",
            "data-notification-count",
            "data-presence-count",
            "auth.logout",
            "data-tooltip",
        ):
            self.assertIn(marker, sidebar)

        for marker in (
            'localStorage.setItem(',
            'root.classList.toggle("sidebar-collapsed"',
            'new CustomEvent("erp:sidebar-change"',
        ):
            self.assertIn(marker, sidebar_script)

    def test_expanded_and_collapsed_visual_rules_share_fixed_geometry(self):
        sidebar_css = self.source("app/static/css/sidebar.css")
        notification_css = self.source(
            "app/static/css/event-notifications.css"
        )

        for marker in (
            "--sidebar-width: 228px",
            "--sidebar-collapsed-width: 72px",
            "--sidebar-bg: #0d2a49",
            "--sidebar-item-height: 42px",
            ".app.sidebar-collapsed .sidebar-count",
            "position: absolute",
            ".app.sidebar-collapsed .sidebar-presence-trigger",
            ".app.sidebar-collapsed .sidebar-user form",
            "flex: 0 0 auto",
            "height: 80px",
            "height: 122px",
            "overflow: clip",
            ".app.sidebar-collapsed .sidebar-toggle",
            "order: -1",
        ):
            self.assertIn(marker, sidebar_css)

        self.assertIn(".notification-bell > svg", notification_css)
        self.assertIn("position: absolute", notification_css)


if __name__ == "__main__":
    unittest.main()
