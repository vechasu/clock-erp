(function () {
    "use strict";

    const existingLifecycle = window.VechasuProductsTabs;
    if (existingLifecycle && typeof existingLifecycle.initialize === "function") {
        existingLifecycle.initialize();
        return;
    }

    const storagePrefix = "vechasu.products.tab-state.v1.";
    const allowedParameters = {
        analytics: [],
        products: [
            "q", "brand", "brand_id", "category", "category_id", "model", "model_id",
            "collection_id", "cell", "date_from", "date_to", "sort_by", "sort_dir", "page",
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
        ],
        collections: ["collection_id", "q", "brand_id", "category_id", "active"]
    };

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

    function productsTabLink(target) {
        if (!(target instanceof Element)) return null;
        return target.closest("[data-products-tab]");
    }

    function prepareLink(event) {
        const link = productsTabLink(event.target);
        if (!link) return;
        saveCurrentState();
        restoreTargetState(link);
    }

    function refreshLinks() {
        saveCurrentState();
        document.querySelectorAll("[data-products-tab]").forEach(
            restoreTargetState
        );
    }

    const lifecycle = {
        initialized: false,
        initialize: function () {
            if (lifecycle.initialized) {
                refreshLinks();
                return;
            }
            lifecycle.initialized = true;
            refreshLinks();
            document.addEventListener("pointerover", prepareLink);
            document.addEventListener("focusin", prepareLink);
            document.addEventListener("click", prepareLink);
            window.addEventListener("pageshow", refreshLinks);
        }
    };
    window.VechasuProductsTabs = lifecycle;

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", lifecycle.initialize, {
            once: true
        });
    } else {
        lifecycle.initialize();
    }
})();
