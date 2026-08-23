(function () {
    "use strict";

    const storagePrefix = "vechasu.products.tab-state.v1.";
    const allowedParameters = {
        analytics: [],
        products: [
            "q", "brand", "brand_id", "category", "category_id", "model", "model_id",
            "cell", "date_from", "date_to", "sort_by", "sort_dir", "page",
            "per_page"
        ],
        out_of_stock: [
            "q", "brand", "brand_id", "category", "category_id", "model", "model_id",
            "cell", "date_from", "date_to", "check_state", "sort_by",
            "sort_dir", "page", "per_page"
        ],
        brands: ["q", "show_empty", "brand_id"],
        categories: [
            "q", "show_empty", "category_id", "sort_by", "sort_dir", "page",
            "per_page"
        ]
    };
    let tabNavigationPending = false;

    function currentView(url) {
        const view = url.searchParams.get("view") || "products";
        return Object.prototype.hasOwnProperty.call(allowedParameters, view)
            ? view
            : "products";
    }

    function compatibleSearch(url, view) {
        const output = new URLSearchParams();
        allowedParameters[view].forEach(function (name) {
            url.searchParams.getAll(name).forEach(function (value) {
                output.append(name, value);
            });
        });
        return output;
    }

    function saveCurrentState() {
        const url = new URL(window.location.href);
        const view = currentView(url);
        try {
            sessionStorage.setItem(
                storagePrefix + view,
                compatibleSearch(url, view).toString()
            );
        } catch (error) {
            // Navigation still works when browser storage is unavailable.
        }
    }

    function restoreTargetState(link) {
        const view = link.dataset.productsTab;
        if (!allowedParameters[view]) return;
        const url = new URL(link.href, window.location.href);
        let saved = "";
        try {
            saved = sessionStorage.getItem(storagePrefix + view) || "";
        } catch (error) {
            saved = "";
        }
        const restored = new URLSearchParams(saved);
        allowedParameters[view].forEach(function (name) {
            url.searchParams.delete(name);
            restored.getAll(name).forEach(function (value) {
                url.searchParams.append(name, value);
            });
        });
        if (view === "products") url.searchParams.delete("view");
        else url.searchParams.set("view", view);
        link.href = url.toString();
    }

    function canHandleTabClick(event, link) {
        if (event.defaultPrevented || event.button !== 0) return false;
        if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) {
            return false;
        }
        if (link.target && link.target !== "_self") return false;
        return new URL(link.href, window.location.href).origin === window.location.origin;
    }

    async function loadTabDocument(targetUrl, addHistoryEntry) {
        if (tabNavigationPending) return;
        tabNavigationPending = true;
        const main = document.querySelector("main");
        if (main) main.setAttribute("aria-busy", "true");
        try {
            const response = await fetch(targetUrl, {
                credentials: "same-origin",
                headers: {"X-Requested-With": "products-tabs"}
            });
            if (!response.ok) throw new Error("Products tab request failed");
            const html = await response.text();
            const responseUrl = new URL(response.url || targetUrl, window.location.href);
            if (responseUrl.origin !== window.location.origin) {
                throw new Error("Products tab redirect changed origin");
            }
            if (addHistoryEntry) history.pushState({}, "", responseUrl);
            document.open();
            document.write(html);
            document.close();
        } catch (error) {
            window.location.assign(targetUrl);
        }
    }

    function handleTabClick(event) {
        const link = event.currentTarget;
        if (!canHandleTabClick(event, link)) return;
        saveCurrentState();
        restoreTargetState(link);
        event.preventDefault();
        loadTabDocument(link.href, true);
    }

    function initialize() {
        saveCurrentState();
        document.querySelectorAll("[data-products-tab]").forEach(function (link) {
            restoreTargetState(link);
            if (link.dataset.productsTabReady === "1") return;
            link.dataset.productsTabReady = "1";
            link.addEventListener("pointerenter", function () {
                saveCurrentState();
                restoreTargetState(link);
            });
            link.addEventListener("focus", function () {
                saveCurrentState();
                restoreTargetState(link);
            });
            link.addEventListener("click", handleTabClick);
        });
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", initialize, { once: true });
    } else {
        initialize();
    }
    window.addEventListener("pageshow", initialize);
    window.addEventListener("popstate", function () {
        loadTabDocument(window.location.href, false);
    });
})();
