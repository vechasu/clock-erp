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

    document.addEventListener("DOMContentLoaded", function () {
        document.querySelectorAll("form[data-erp-pending], form[data-single-submit]")
            .forEach(function (form) { rememberBaseline(form); });
    });

    global.addEventListener("pageshow", function () {
        document.querySelectorAll("form[data-erp-pending], form[data-single-submit]").forEach(reset);
        document.querySelectorAll("[data-erp-async-pending='1']").forEach(function (control) {
            delete control.dataset.erpAsyncPending;
            control.disabled = false;
            control.removeAttribute("aria-busy");
        });
    });

    global.VechasuStates = Object.freeze({begin, reset, run, setFieldError});
})(window);
