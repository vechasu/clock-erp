(function (global) {
    "use strict";

    if (global.VechasuStates) return;

    const pendingForms = new Set();

    function rememberBaseline(form, button) {
        if (!form) return;
        const controls = button
            ? [button]
            : Array.from(form.querySelectorAll("button[type='submit'], input[type='submit']"));
        controls.forEach(function (control) {
            if (control.dataset.erpBaselineHtml !== undefined) return;
            control.dataset.erpBaselineHtml = control.tagName === "INPUT"
                ? control.value : control.innerHTML;
            control.dataset.erpBaselineDisabled = control.disabled ? "1" : "0";
        });
    }

    function label(button) {
        if (!button) return "Операция выполняется…";
        return String(
            button.dataset.pendingLabel
            || button.form?.dataset.pendingLabel
            || "Операция выполняется…"
        ).trim();
    }

    function rememberButton(button) {
        if (!button || button.dataset.erpIdleHtml !== undefined) return;
        button.dataset.erpIdleHtml = button.innerHTML;
        button.dataset.erpIdleDisabled = button.disabled ? "1" : "0";
        const width = button.getBoundingClientRect().width;
        if (width > 0) {
            button.dataset.erpIdleMinWidth = button.style.minWidth || "";
            button.style.minWidth = Math.ceil(width) + "px";
        }
    }

    function begin(form, submitter) {
        if (!form || pendingForms.has(form)) return false;
        const button = submitter
            || form.querySelector("button[type='submit'], input[type='submit']");
        rememberBaseline(form, button);
        rememberButton(button);
        pendingForms.add(form);
        form.dataset.erpPendingActive = "1";
        form.classList.add("is-erp-pending");
        form.setAttribute("aria-busy", "true");
        if (button) {
            button.disabled = true;
            button.setAttribute("aria-busy", "true");
            if (button.tagName === "INPUT") button.value = label(button);
            else {
                button.innerHTML = "";
                const indicator = document.createElement("span");
                indicator.dataset.erpPendingIndicator = "";
                indicator.textContent = label(button);
                button.appendChild(indicator);
            }
        }
        return true;
    }

    function reset(form) {
        if (!form) return;
        pendingForms.delete(form);
        delete form.dataset.erpSubmitQueued;
        delete form.dataset.erpPendingActive;
        delete form.dataset.submitting;
        delete form.dataset.submitted;
        form.classList.remove("is-erp-pending");
        form.removeAttribute("aria-busy");
        form.querySelectorAll("[data-erp-idle-html]").forEach(function (button) {
            if (button.tagName === "INPUT") button.value = button.dataset.erpIdleHtml;
            else button.innerHTML = button.dataset.erpIdleHtml;
            button.disabled = button.dataset.erpIdleDisabled === "1";
            button.removeAttribute("aria-busy");
            button.style.minWidth = button.dataset.erpIdleMinWidth || "";
            delete button.dataset.erpIdleHtml;
            delete button.dataset.erpIdleDisabled;
            delete button.dataset.erpIdleMinWidth;
        });
        form.querySelectorAll("[data-erp-baseline-html]").forEach(function (button) {
            if (button.tagName === "INPUT") button.value = button.dataset.erpBaselineHtml;
            else button.innerHTML = button.dataset.erpBaselineHtml;
            button.disabled = button.dataset.erpBaselineDisabled === "1";
            button.removeAttribute("aria-busy");
        });
    }

    async function run(control, pendingLabel, operation) {
        if (!control || control.dataset.erpAsyncPending === "1") return undefined;
        const owner = control.form || control.closest("form") || control;
        rememberButton(control);
        control.dataset.erpAsyncPending = "1";
        control.disabled = true;
        control.setAttribute("aria-busy", "true");
        if (control.tagName !== "INPUT") {
            control.innerHTML = "";
            const indicator = document.createElement("span");
            indicator.dataset.erpPendingIndicator = "";
            indicator.textContent = pendingLabel || label(control);
            control.appendChild(indicator);
        }
        owner.setAttribute("aria-busy", "true");
        try {
            return await operation();
        } finally {
            delete control.dataset.erpAsyncPending;
            owner.removeAttribute("aria-busy");
            if (control.dataset.erpIdleHtml !== undefined) {
                control.innerHTML = control.dataset.erpIdleHtml;
                control.disabled = control.dataset.erpIdleDisabled === "1";
                control.removeAttribute("aria-busy");
                control.style.minWidth = control.dataset.erpIdleMinWidth || "";
                delete control.dataset.erpIdleHtml;
                delete control.dataset.erpIdleDisabled;
                delete control.dataset.erpIdleMinWidth;
            }
        }
    }

    function setFieldError(field, message) {
        if (!field) return;
        const id = field.getAttribute("aria-describedby");
        const node = id ? document.getElementById(id.split(/\s+/).pop()) : null;
        field.setAttribute("aria-invalid", message ? "true" : "false");
        if (node) node.textContent = message || "";
    }

    function accessibleTableName(table, index) {
        const explicit = table.getAttribute("aria-label")
            || table.getAttribute("aria-labelledby");
        if (explicit) return explicit;
        const heading = document.querySelector("main h1");
        return (heading ? heading.textContent.trim() : "Данные")
            + (index ? " — таблица " + (index + 1) : " — таблица данных");
    }

    function updateSortableHeader(header) {
        const control = header.querySelector(
            "button[data-sort], a[data-sort], .receipt-sort-button"
        );
        if (!control) return;
        const active = control.classList.contains("is-active")
            || Boolean(control.querySelector(".is-active"));
        let direction = "none";
        if (active) {
            const value = String(
                control.dataset.direction
                || control.getAttribute("data-sort-direction")
                || control.textContent
                || ""
            ).toLowerCase();
            direction = value.includes("desc") || value.includes("↓")
                ? "descending" : "ascending";
        }
        header.setAttribute("aria-sort", direction);
    }

    function initializeTables() {
        document.querySelectorAll("table").forEach(function (table, index) {
            if (!table.hasAttribute("aria-label")
                && !table.hasAttribute("aria-labelledby")) {
                table.setAttribute("aria-label", accessibleTableName(table, index));
            }
            table.querySelectorAll("thead th").forEach(function (header) {
                if (!header.hasAttribute("scope")) {
                    header.setAttribute("scope", "col");
                }
                updateSortableHeader(header);
            });
            table.querySelectorAll("tbody th").forEach(function (header) {
                if (!header.hasAttribute("scope")) {
                    header.setAttribute("scope", "row");
                }
            });
        });
    }

    function initializeScrollRegions() {
        const candidates = document.querySelectorAll(
            "[class*='table-wrap'], [class*='table-scroll'], "
            + ".table-container, [data-erp-scroll-region]"
        );
        candidates.forEach(function (region) {
            const styles = global.getComputedStyle(region);
            const scrollable = region.scrollWidth > region.clientWidth + 1
                && ["auto", "scroll"].includes(styles.overflowX);
            if (!scrollable) return;
            region.classList.add("erp-scroll-region");
            if (!region.hasAttribute("tabindex")) region.tabIndex = 0;
            if (!region.hasAttribute("role")) region.setAttribute("role", "region");
            if (!region.hasAttribute("aria-label")
                && !region.hasAttribute("aria-labelledby")) {
                const table = region.querySelector("table");
                region.setAttribute(
                    "aria-label",
                    (table && table.getAttribute("aria-label"))
                    || "Таблица с горизонтальной прокруткой"
                );
            }
        });
    }

    function initializeAccessibility() {
        const main = document.querySelector("main");
        const skipLink = document.querySelector(".erp-skip-link");
        if (main && !main.id) main.id = "main-content";
        if (main && !main.hasAttribute("tabindex")) main.tabIndex = -1;
        if (skipLink && main) {
            skipLink.addEventListener("click", function () {
                global.setTimeout(function () {
                    main.focus({preventScroll: true});
                }, 0);
            });
        }

        document.querySelectorAll(
            "[role='status'], [role='alert'], [aria-live], "
            + ".erp-loading-state, .erp-success-state, .erp-error-state"
        ).forEach(function (region) {
            if (!region.hasAttribute("role")) {
                region.setAttribute(
                    "role",
                    region.classList.contains("erp-error-state")
                        ? "alert" : "status"
                );
            }
            if (!region.hasAttribute("aria-live")) {
                region.setAttribute(
                    "aria-live",
                    region.getAttribute("role") === "alert"
                        ? "assertive" : "polite"
                );
            }
            if (!region.hasAttribute("aria-atomic")) {
                region.setAttribute("aria-atomic", "true");
            }
        });

        document.querySelectorAll('[aria-disabled="true"]').forEach(function (control) {
            if (!/^(BUTTON|INPUT|SELECT|TEXTAREA)$/.test(control.tagName)
                && !control.hasAttribute("tabindex")) control.tabIndex = -1;
        });

        initializeTables();
        initializeScrollRegions();

        const formError = document.querySelector(
            ".auth-alert[role='alert']:not(:empty), "
            + ".erp-form-error[data-focus-error]:not(:empty)"
        );
        if (formError && formError.getClientRects().length) {
            if (!formError.hasAttribute("tabindex")) formError.tabIndex = -1;
            formError.focus();
        }
    }

    document.addEventListener("submit", function (event) {
        const form = event.target;
        if (!(form instanceof HTMLFormElement)
            || form.method.toUpperCase() === "GET"
            || !form.matches("[data-erp-pending], [data-single-submit]")) return;

        if (form.dataset.erpPendingActive === "1" || form.dataset.erpSubmitQueued === "1") {
            event.preventDefault();
            return;
        }
        form.dataset.erpSubmitQueued = "1";
        const submitter = event.submitter
            || form.querySelector("button[type='submit'], input[type='submit']");
        rememberBaseline(form, submitter);
        global.setTimeout(function () {
            if (event.defaultPrevented || !form.isConnected) {
                delete form.dataset.erpSubmitQueued;
                return;
            }
            begin(form, submitter);
        }, 0);
    }, true);

    function initializeDocument() {
        initializeAccessibility();
        document.querySelectorAll("form[data-erp-pending], form[data-single-submit]")
            .forEach(function (form) { rememberBaseline(form); });
    }

    if (document.readyState === "loading") {
        document.addEventListener(
            "DOMContentLoaded",
            initializeDocument,
            {once: true}
        );
    } else {
        initializeDocument();
    }

    global.addEventListener("resize", function () {
        global.requestAnimationFrame(initializeScrollRegions);
    });

    global.addEventListener("pageshow", function (event) {
        document.querySelectorAll("form[data-erp-pending], form[data-single-submit]").forEach(function (form) {
            if (event.persisted
                || form.dataset.erpPendingActive === "1"
                || form.dataset.erpSubmitQueued === "1"
                || form.dataset.submitting === "1"
                || form.dataset.submitted === "1") reset(form);
        });
        document.querySelectorAll("[data-erp-async-pending='1']").forEach(function (control) {
            delete control.dataset.erpAsyncPending;
            control.disabled = false;
            control.removeAttribute("aria-busy");
        });
    });

    global.VechasuStates = Object.freeze({
        begin,
        reset,
        run,
        setFieldError,
        initializeAccessibility,
    });
})(window);
