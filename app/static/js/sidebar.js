(() => {
    "use strict";

    const storageKey = "ttt-erp-sidebar-collapsed";
    const app = document.querySelector(".app");

    const readCollapsedState = () => {
        try {
            return localStorage.getItem(storageKey) === "1";
        } catch (error) {
            return false;
        }
    };

    if (app) {
        app.classList.toggle(
            "sidebar-collapsed",
            readCollapsedState()
        );
    }

    const initializeSidebar = () => {
        const root = document.querySelector(".app");
        const toggle = document.getElementById("sidebarToggle");

        if (!root || !toggle) {
            return;
        }

        const applySidebarState = (collapsed) => {
            root.classList.toggle("sidebar-collapsed", collapsed);
            toggle.classList.toggle("is-collapsed", collapsed);
            toggle.setAttribute("aria-expanded", String(!collapsed));

            const label = collapsed
                ? "Развернуть боковую панель"
                : "Свернуть боковую панель";

            toggle.setAttribute("aria-label", label);
            toggle.title = label;
        };

        applySidebarState(root.classList.contains("sidebar-collapsed"));
        requestAnimationFrame(() => {
            requestAnimationFrame(() => {
                root.classList.add("sidebar-ready");
            });
        });

        toggle.addEventListener("click", () => {
            const collapsed = !root.classList.contains(
                "sidebar-collapsed"
            );

            applySidebarState(collapsed);

            try {
                localStorage.setItem(
                    storageKey,
                    collapsed ? "1" : "0"
                );
            } catch (error) {
                // Сворачивание остаётся доступным без сохранения.
            }
        });
    };

    const initializeMobileMenu = () => {
        const trigger = document.getElementById(
            "mobileErpMoreTrigger"
        );
        const sheet = document.getElementById(
            "mobileErpMoreSheet"
        );
        const backdrop = document.getElementById(
            "mobileErpMoreBackdrop"
        );

        if (!trigger || !sheet || !backdrop) {
            return;
        }

        const focusableSelector =
            "a[href], button:not([disabled])";

        const closeMenu = (restoreFocus = true) => {
            sheet.classList.remove("is-open");
            backdrop.classList.remove("is-open");
            sheet.hidden = true;
            trigger.setAttribute("aria-expanded", "false");
            document.body.classList.remove("mobile-erp-menu-open");

            if (restoreFocus) {
                trigger.focus();
            }
        };

        const openMenu = () => {
            sheet.hidden = false;
            sheet.classList.add("is-open");
            backdrop.classList.add("is-open");
            trigger.setAttribute("aria-expanded", "true");
            document.body.classList.add("mobile-erp-menu-open");
            sheet.querySelector(focusableSelector)?.focus();
        };

        trigger.addEventListener("click", () => {
            if (sheet.hidden) {
                openMenu();
            } else {
                closeMenu();
            }
        });

        backdrop.addEventListener("click", () => closeMenu());

        document.addEventListener("keydown", (event) => {
            if (sheet.hidden) {
                return;
            }

            if (event.key === "Escape") {
                closeMenu();
                return;
            }

            if (event.key !== "Tab") {
                return;
            }

            const focusable = Array.from(
                sheet.querySelectorAll(focusableSelector)
            ).filter(
                (element) => element.getClientRects().length > 0
            );
            const first = focusable[0];
            const last = focusable[focusable.length - 1];

            if (event.shiftKey && document.activeElement === first) {
                event.preventDefault();
                last?.focus();
            } else if (
                !event.shiftKey
                && document.activeElement === last
            ) {
                event.preventDefault();
                first?.focus();
            }
        });
    };

    const initializeDialogFocusTrap = () => {
        document.addEventListener("keydown", (event) => {
            if (event.key !== "Tab") {
                return;
            }

            const dialog = document.querySelector(
                '.modal.is-open[aria-modal="true"], '
                + '.drawer.open[aria-modal="true"], '
                + '.map-modal.open[aria-modal="true"]'
            );

            if (!dialog) {
                return;
            }

            const focusable = Array.from(dialog.querySelectorAll(
                'a[href], button:not([disabled]), '
                + 'input:not([disabled]), select:not([disabled]), '
                + 'textarea:not([disabled]), '
                + '[tabindex]:not([tabindex="-1"])'
            )).filter(
                (element) => element.getClientRects().length > 0
            );

            if (!focusable.length) {
                event.preventDefault();
                dialog.focus();
                return;
            }

            const first = focusable[0];
            const last = focusable[focusable.length - 1];

            if (event.shiftKey && document.activeElement === first) {
                event.preventDefault();
                last.focus();
            } else if (
                !event.shiftKey
                && document.activeElement === last
            ) {
                event.preventDefault();
                first.focus();
            }
        });
    };

    const initialize = () => {
        initializeSidebar();
        initializeMobileMenu();
        initializeDialogFocusTrap();
    };

    if (document.readyState === "loading") {
        document.addEventListener(
            "DOMContentLoaded",
            initialize,
            {once: true}
        );
    } else {
        initialize();
    }
})();
