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
    const returnFocus = new WeakMap();
    let inerted = [];

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

    function restoreBackground() {
        inerted.forEach(function (record) {
            record.element.inert = record.inert;
        });
        inerted = [];
    }

    function hideBackground(modal) {
        restoreBackground();
        let child = modal;
        let parent = modal.parentElement;
        while (parent && parent !== document.body) {
            Array.from(parent.children).forEach(function (sibling) {
                if (sibling === child || sibling.contains(modal)) return;
                inerted.push({
                    element: sibling,
                    inert: sibling.inert,
                });
                sibling.inert = true;
            });
            child = parent;
            parent = parent.parentElement;
        }
    }

    function opened(modal) {
        return modal.classList.contains("is-open")
            && modal.getAttribute("aria-hidden") !== "true";
    }

    function synchronizeModal() {
        const modal = activeModal();
        if (!modal) {
            restoreBackground();
            document.body.classList.remove("modal-open");
            document.querySelectorAll("[data-erp-modal-shell]")
                .forEach(function (candidate) {
                    const target = returnFocus.get(candidate);
                    if (target && target.isConnected) target.focus();
                    returnFocus.delete(candidate);
                });
            return;
        }

        document.body.classList.add("modal-open");
        if (!returnFocus.has(modal) && !modal.contains(document.activeElement)) {
            returnFocus.set(modal, document.activeElement);
        }
        hideBackground(modal);
        globalThis.requestAnimationFrame(function () {
            if (!opened(modal) || modal.contains(document.activeElement)) return;
            const preferred = modal.querySelector(
                "[autofocus], [data-initial-focus], "
                + "input:not([type='hidden']):not([disabled]), "
                + "select:not([disabled]), textarea:not([disabled]), "
                + ".modal-close, button:not([disabled])"
            );
            (preferred || modal.querySelector(".modal-dialog") || modal).focus();
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
            if (modal.getAttribute("aria-busy") === "true"
                || modal.hasAttribute("data-erp-escape-locked")) {
                event.preventDefault();
                event.stopImmediatePropagation();
                return;
            }
            const close = modal.querySelector(
                "[data-close-modal], [data-close-sale-dialog], "
                + "[data-close-receipt-modal], .modal-close, "
                + "button[value='cancel'], button[formmethod='dialog']"
            );
            if (close) close.click();
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
        synchronizeModal();
        const observer = new MutationObserver(synchronizeModal);
        document.querySelectorAll("[data-erp-modal-shell]")
            .forEach(function (modal) {
                observer.observe(modal, {
                    attributes: true,
                    attributeFilter: ["class", "aria-hidden", "hidden"],
                });
            });
    });
})();
