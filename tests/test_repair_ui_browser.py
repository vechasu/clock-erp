import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from werkzeug.serving import make_server

from app import web
from app.services.repair_cases import migrate_repair_case


def browser_repair_case():
    return migrate_repair_case({
        "id": "repair-browser-1",
        "repair_number": "R-2026-9001",
        "created_at": "2026-07-20 12:00",
        "updated_at": "2026-07-20 12:00",
        "status": "at_master",
        "request_type": "warranty_repair",
        "location": "with_master",
        "order_source": "our",
        "order_number": "ORDER-WITH-A-LONG-NUMBER-9001",
        "client_name": "Иван Петров с длинным отображаемым именем",
        "client_phone": "+7 999 000-00-00",
        "communication_channel": "telegram",
        "contact": "@customer_with_a_long_contact_identifier",
        "product_name": (
            "Vechasu Expedition Chronograph Limited Edition "
            "с длинным названием модели"
        ),
        "brand": "Vechasu",
        "model": "Expedition Chronograph Limited Edition",
        "problem": (
            "Часы периодически останавливаются и требуют подробной "
            "диагностики механизма с проверкой длительного хода."
        ),
        "request_at": "2026-07-20",
        "history": [{
            "timestamp": "2026-07-20 12:00",
            "actor": "Максим",
            "action": "Создано обращение",
            "comment": "Первичная диагностика запланирована",
        }],
    })


class InjectRepairBrowserCheck:
    def __init__(self, application):
        self.application = application

    def __call__(self, environ, start_response):
        response = {}

        def capture(status, headers, exc_info=None):
            response["status"] = status
            response["headers"] = headers
            response["exc_info"] = exc_info

        iterable = self.application(environ, capture)

        try:
            body = b"".join(iterable)
        finally:
            close = getattr(iterable, "close", None)
            if close:
                close()

        if (
            environ.get("PATH_INFO") == "/repair"
            and b"</body>" in body
        ):
            body = body.replace(
                b"</body>",
                self.browser_script() + b"</body>",
                1,
            )
            headers = [
                (name, value)
                for name, value in response["headers"]
                if name.lower() != "content-length"
            ]
            headers.append(("Content-Length", str(len(body))))
            response["headers"] = headers

        start_response(
            response["status"],
            response["headers"],
            response["exc_info"],
        )
        return [body]

    @staticmethod
    def browser_script():
        return r"""
<script>
document.addEventListener("DOMContentLoaded", () => {
    window.setTimeout(() => {
        const failures = [];
        const check = (condition, label) => {
            if (!condition) failures.push(label);
        };
        const visible = (element) => (
            element
            && !element.hidden
            && getComputedStyle(element).display !== "none"
        );

        check(
            document.documentElement.scrollWidth
                <= window.innerWidth + 1,
            "document-overflow"
        );
        check(
            document.querySelector(".repair-hero")
                .getBoundingClientRect().height <= 74,
            "hero-height"
        );
        check(
            Math.max(...Array.from(
                document.querySelectorAll(".metric")
            ).map((item) => item.getBoundingClientRect().height))
                <= 64,
            "metric-height"
        );

        const more = document.getElementById(
            "toggleRepairAdditionalFilters"
        );
        const panel = document.getElementById(
            "repairAdditionalFilters"
        );
        check(panel.hidden, "additional-default");
        more.click();
        check(
            !panel.hidden
                && more.getAttribute("aria-expanded") === "true",
            "additional-open"
        );

        const type = document.querySelector('[name="type"]');
        type.value = "warranty_repair";
        type.dispatchEvent(new Event("change", {bubbles: true}));
        check(
            more.textContent.includes("1"),
            "additional-count"
        );
        more.click();
        check(
            panel.hidden
                && type.value === "warranty_repair",
            "additional-preserve"
        );

        const periodRoot = document.getElementById(
            "repairDateFilter"
        );
        const periodTrigger = periodRoot.querySelector(
            ".warehouse-date-trigger"
        );
        periodTrigger.click();
        const popup = periodRoot.querySelector(
            ".warehouse-calendar-popup"
        );
        const popupRect = popup.getBoundingClientRect();
        check(
            visible(popup)
                && popupRect.left >= -1
                && popupRect.right <= window.innerWidth + 1,
            "period-popup"
        );
        let days = Array.from(periodRoot.querySelectorAll(
            ".warehouse-calendar-day:not(.is-outside)"
        ));
        days[4].click();
        check(visible(popup), "period-first-date");
        days = Array.from(periodRoot.querySelectorAll(
            ".warehouse-calendar-day:not(.is-outside)"
        ));
        days[10].click();
        periodRoot.querySelector("[data-calendar-apply]").click();
        check(
            document.querySelector('[name="date_from"]').value
                && document.querySelector('[name="date_to"]').value
                && periodTrigger.textContent.trim() !== "Период",
            "period-apply"
        );

        const reset = document.getElementById(
            "resetRepairFilters"
        );
        check(!reset.hidden, "reset-visible");
        reset.click();
        check(
            !document.querySelector('[name="date_from"]').value
                && !document.querySelector('[name="date_to"]').value
                && !type.value
                && document.querySelector('[name="sort"]').value
                    === "date_desc"
                && reset.hidden,
            "reset-complete"
        );

        const desktop = window.innerWidth > 900;
        const tableCard = document.querySelector(".table-card");
        const mobileCards = document.getElementById(
            "repairMobileCards"
        );
        check(
            desktop
                ? visible(tableCard)
                : visible(mobileCards),
            "responsive-content"
        );
        if (desktop) {
            const actionsCell = document.querySelector(
                ".repair-table td:last-child"
            );
            check(
                getComputedStyle(actionsCell).position === "sticky",
                "sticky-actions"
            );
        }

        if (window.innerWidth > 767) {
            const app = document.querySelector(".app");
            const settings = Array.from(
                document.querySelectorAll(".sidebar-link")
            ).find((link) => link.dataset.tooltip === "Настройки");
            const tooltip = document.getElementById("sidebarTooltip");

            app.classList.add("sidebar-collapsed");
            settings.dispatchEvent(new Event("pointerenter"));
            check(
                !tooltip.hidden
                    && tooltip.textContent === "Настройки",
                "tooltip-open"
            );
            document.dispatchEvent(
                new KeyboardEvent("keydown", {key: "Escape"})
            );
            check(tooltip.hidden, "tooltip-escape");
            app.classList.remove("sidebar-collapsed");
            settings.dispatchEvent(new Event("pointerenter"));
            check(tooltip.hidden, "tooltip-expanded");
        }

        document.documentElement.dataset.repairUiE2e =
            failures.length ? failures.join(",") : "pass";
    }, 120);
});
</script>
""".encode("utf-8")


class RepairUiBrowserTest(unittest.TestCase):
    def find_chrome(self):
        candidates = (
            os.environ.get("CHROME_BIN"),
            shutil.which("google-chrome"),
            shutil.which("google-chrome-stable"),
            shutil.which("chromium"),
            shutil.which("chromium-browser"),
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        )
        return next(
            (
                candidate
                for candidate in candidates
                if candidate and Path(candidate).is_file()
            ),
            None,
        )

    def run_browser_check(self, chrome, width, height):
        original_testing = web.app.testing
        web.app.testing = True
        wrapped_app = InjectRepairBrowserCheck(web.app)
        server = make_server("127.0.0.1", 0, wrapped_app)
        thread = threading.Thread(
            target=server.serve_forever,
            daemon=True,
        )

        try:
            with mock.patch.object(
                web,
                "load_repair_cases",
                return_value=[browser_repair_case()],
            ), mock.patch.object(
                web,
                "build_repair_catalog_items",
                return_value=[],
            ), tempfile.TemporaryDirectory() as profile:
                thread.start()
                result = subprocess.run(
                    [
                        chrome,
                        "--headless=new",
                        "--no-sandbox",
                        "--disable-gpu",
                        "--disable-dev-shm-usage",
                        f"--user-data-dir={profile}",
                        f"--window-size={width},{height}",
                        "--virtual-time-budget=2500",
                        "--dump-dom",
                        (
                            f"http://127.0.0.1:{server.server_port}"
                            "/repair"
                        ),
                    ],
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
        finally:
            server.shutdown()
            thread.join(timeout=5)
            web.app.testing = original_testing

        self.assertEqual(
            result.returncode,
            0,
            result.stderr[-2000:],
        )
        self.assertIn(
            'data-repair-ui-e2e="pass"',
            result.stdout,
            result.stdout[-3000:],
        )

    def test_filters_tooltip_and_layout_on_key_viewports(self):
        if sys.platform == "darwin":
            self.skipTest(
                "macOS Chrome does not reliably exit after --dump-dom"
            )

        chrome = self.find_chrome()
        if not chrome:
            self.skipTest("Chrome/Chromium is unavailable")

        for width, height in (
            (1440, 1000),
            (1024, 900),
            (768, 900),
            (390, 844),
            (320, 700),
        ):
            with self.subTest(width=width):
                self.run_browser_check(chrome, width, height)


if __name__ == "__main__":
    unittest.main()
