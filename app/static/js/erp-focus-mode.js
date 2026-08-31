(function () {
    "use strict";

    const focusableState = new WeakMap();

    function safeRead(storageKey) {
        try {
            return window.localStorage.getItem(storageKey) === "1";
        } catch (error) {
            return false;
        }
    }

    function safeWrite(storageKey, enabled) {
        try {
            window.localStorage.setItem(storageKey, enabled ? "1" : "0");
        } catch (error) {
            // Focus mode remains usable when storage is unavailable.
        }
    }

    function rememberAndHide(element) {
        if (!focusableState.has(element)) {
            focusableState.set(element, {
                hidden: element.hidden,
                inert: element.inert,
                ariaHidden: element.getAttribute("aria-hidden"),
            });
        }
        element.hidden = true;
        element.inert = true;
        element.setAttribute("aria-hidden", "true");
    }

    function restoreElement(element) {
        const previous = focusableState.get(element);
        if (!previous) {
            return;
        }
        element.hidden = previous.hidden;
        element.inert = previous.inert;
        if (previous.ariaHidden === null) {
            element.removeAttribute("aria-hidden");
        } else {
            element.setAttribute("aria-hidden", previous.ariaHidden);
        }
        focusableState.delete(element);
    }

    function elementsForSelectors(selectors) {
        if (!selectors) {
            return [];
        }
        try {
            return Array.from(document.querySelectorAll(selectors));
        } catch (error) {
            return [];
        }
    }

    function initialize(root) {
        if (root.dataset.focusModeReady === "1") {
            return root.erpFocusModeController || null;
        }

        const toggle = root.querySelector("[data-erp-focus-mode-toggle]");
        const storageKey = root.dataset.focusModeStorageKey;
        const subject = root.dataset.focusModeSubject || "таблицу продаж";
        if (!toggle || !storageKey) {
            return null;
        }
        const labelSuffix = toggle.dataset.focusModeLabelSuffix || "";

        root.dataset.focusModeReady = "1";
        const hiddenElements = elementsForSelectors(
            root.dataset.focusModeHide
        );
        const overlaySelector = root.dataset.focusModeOverlays || "";
        const toolbar = root.querySelector("[data-erp-focus-mode-toolbar]");
        const expandIcon = toggle.querySelector("[data-focus-expand-icon]");
        const collapseIcon = toggle.querySelector("[data-focus-collapse-icon]");
        const label = toggle.querySelector("[data-focus-mode-label]");
        let enabled = false;

        function updateToolbarHeight() {
            if (!toolbar) {
                return;
            }
            root.style.setProperty(
                "--erp-focus-toolbar-height",
                toolbar.getBoundingClientRect().height + "px"
            );
        }

        const toolbarObserver = typeof ResizeObserver === "function"
            ? new ResizeObserver(updateToolbarHeight)
            : null;
        toolbarObserver?.observe(toolbar);

        function hasOpenOverlay() {
            if (!overlaySelector) {
                return false;
            }
            try {
                return Array.from(document.querySelectorAll(overlaySelector))
                    .some(function (element) {
                        return !element.hidden
                            && element.getAttribute("aria-hidden") !== "true"
                            && element.getClientRects().length > 0;
                    });
            } catch (error) {
                return false;
            }
        }

        function apply(nextEnabled, options) {
            const settings = Object.assign({
                persist: true,
                restoreFocus: false,
            }, options || {});
            enabled = Boolean(nextEnabled);
            root.classList.toggle("erp-focus-mode", enabled);
            hiddenElements.forEach(enabled ? rememberAndHide : restoreElement);
            toggle.setAttribute("aria-pressed", String(enabled));
            toggle.setAttribute("aria-expanded", String(enabled));

            const action = enabled ? "Свернуть" : "Развернуть";
            toggle.setAttribute("aria-label", action + " " + subject);
            toggle.title = action + " " + subject;
            if (label) {
                label.textContent = action + labelSuffix;
            }
            if (expandIcon) {
                expandIcon.hidden = enabled;
            }
            if (collapseIcon) {
                collapseIcon.hidden = !enabled;
            }

            if (settings.persist) {
                safeWrite(storageKey, enabled);
            }
            updateToolbarHeight();
            window.dispatchEvent(new Event("resize"));
            root.dispatchEvent(new CustomEvent("erp:focus-mode-change", {
                detail: {enabled: enabled},
            }));

            if (!enabled && settings.restoreFocus) {
                requestAnimationFrame(function () {
                    toggle.focus();
                });
            }
        }

        toggle.addEventListener("click", function () {
            apply(!enabled, {restoreFocus: enabled});
        });

        document.addEventListener("keydown", function (event) {
            if (event.key !== "Escape" || !enabled || hasOpenOverlay()) {
                return;
            }
            event.preventDefault();
            event.stopPropagation();
            apply(false, {restoreFocus: true});
        }, true);

        window.addEventListener("pagehide", function () {
            apply(false, {persist: false, restoreFocus: false});
        });
        window.addEventListener("pageshow", function (event) {
            if (event.persisted && safeRead(storageKey)) {
                apply(true, {persist: false, restoreFocus: false});
            }
        });

        const controller = {
            apply: apply,
            isEnabled: function () {
                return enabled;
            },
            hasOpenOverlay: hasOpenOverlay,
        };
        root.erpFocusModeController = controller;
        apply(safeRead(storageKey), {
            persist: false,
            restoreFocus: false,
        });
        return controller;
    }

    function initializeAll() {
        document.querySelectorAll("[data-erp-focus-mode]")
            .forEach(initialize);
    }

    window.createErpFocusMode = initialize;
    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", initializeAll);
    } else {
        initializeAll();
    }
})();
