(function () {
    "use strict";

    const modalSelector = "[data-erp-modal-shell].is-open";
    const focusableSelector = [
        "button:not([disabled])",
        "a[href]",
        "input:not([disabled]):not([type=hidden])",
        "select:not([disabled])",
        "textarea:not([disabled])",
        "[tabindex]:not([tabindex='-1'])",
    ].join(",");

    function activeModal() {
        const modals = Array.from(
            document.querySelectorAll(modalSelector)
        );
        return modals[modals.length - 1] || null;
    }

    function focusableElements(modal) {
        return Array.from(modal.querySelectorAll(focusableSelector))
            .filter(function (element) {
                return element.getClientRects().length > 0
                    && element.getAttribute("aria-hidden") !== "true";
            });
    }

    document.addEventListener("click", function (event) {
        const modal = event.target.closest("[data-erp-modal-lock]");
        if (modal && event.target === modal) {
            event.preventDefault();
            event.stopPropagation();
        }
    }, true);

    document.addEventListener("keydown", function (event) {
        const modal = activeModal();
        if (!modal) {
            return;
        }

        if (event.key === "Escape") {
            event.preventDefault();
            event.stopImmediatePropagation();
            return;
        }

        if (event.key !== "Tab") {
            return;
        }

        const focusable = focusableElements(modal);
        if (!focusable.length) {
            event.preventDefault();
            modal.querySelector(".modal-dialog")?.focus();
            return;
        }

        const first = focusable[0];
        const last = focusable[focusable.length - 1];
        if (event.shiftKey && document.activeElement === first) {
            event.preventDefault();
            last.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
            event.preventDefault();
            first.focus();
        }
    }, true);

    document.addEventListener("DOMContentLoaded", function () {
        document.querySelectorAll("[data-erp-modal-shell] .modal-dialog")
            .forEach(function (dialog) {
                if (!dialog.hasAttribute("tabindex")) {
                    dialog.setAttribute("tabindex", "-1");
                }
            });
        if (activeModal()) {
            document.body.classList.add("modal-open");
        }
    });
})();
