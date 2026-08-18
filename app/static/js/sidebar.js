(() => {
    "use strict";

    const storageKey = "ttt-erp-sidebar-collapsed";
    const app = document.querySelector(".app");
    let hideSidebarTooltip = () => {};

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
            hideSidebarTooltip();
            root.dispatchEvent(new CustomEvent("erp:sidebar-change", {
                detail: {collapsed: collapsed},
            }));
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

    const initializeSidebarTooltip = () => {
        const root = document.querySelector(".app");
        const tooltip = document.getElementById("sidebarTooltip");
        const links = Array.from(
            document.querySelectorAll(".sidebar-link[data-tooltip]")
        );
        let activeLink = null;

        if (!root || !tooltip || !links.length) {
            return () => {};
        }

        const hide = () => {
            if (activeLink) {
                activeLink.removeAttribute("aria-describedby");
            }

            activeLink = null;
            tooltip.hidden = true;
            tooltip.textContent = "";
            tooltip.style.removeProperty("top");
            tooltip.style.removeProperty("left");
        };

        const show = (link) => {
            if (
                !root.classList.contains("sidebar-collapsed")
                || window.matchMedia("(max-width: 767px)").matches
            ) {
                hide();
                return;
            }

            const label = link.dataset.tooltip || "";

            if (!label) {
                hide();
                return;
            }

            if (activeLink && activeLink !== link) {
                activeLink.removeAttribute("aria-describedby");
            }

            activeLink = link;
            tooltip.textContent = label;
            tooltip.hidden = false;
            link.setAttribute("aria-describedby", tooltip.id);

            const linkRect = link.getBoundingClientRect();
            const tooltipRect = tooltip.getBoundingClientRect();
            const top = Math.max(
                8,
                Math.min(
                    window.innerHeight - tooltipRect.height - 8,
                    linkRect.top
                    + (linkRect.height - tooltipRect.height) / 2
                )
            );
            const left = Math.min(
                window.innerWidth - tooltipRect.width - 8,
                linkRect.right + 9
            );

            tooltip.style.top = `${top}px`;
            tooltip.style.left = `${left}px`;
        };

        links.forEach((link) => {
            link.addEventListener("pointerenter", () => show(link));
            link.addEventListener("pointerleave", hide);
            link.addEventListener("focus", () => show(link));
            link.addEventListener("blur", hide);
            link.addEventListener("click", hide);
        });

        document.addEventListener("keydown", (event) => {
            if (event.key === "Escape") {
                hide();
            }
        });
        document.addEventListener("pointerdown", hide, true);
        document.addEventListener("scroll", hide, true);
        window.addEventListener("resize", hide);
        window.addEventListener("pagehide", hide);
        document.addEventListener("visibilitychange", () => {
            if (document.hidden) {
                hide();
            }
        });

        return hide;
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
        hideSidebarTooltip = initializeSidebarTooltip();
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
