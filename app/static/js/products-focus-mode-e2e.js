(function () {
    "use strict";

    window.addEventListener("load", function () {
        const documentRoot = document.documentElement;
        const root = document.querySelector("[data-erp-focus-mode]");
        const toggle = document.getElementById("warehouseFocusModeToggle");
        const search = document.getElementById("warehouseSearchInput");
        const columns = document.getElementById("warehouseColumnSettingsTrigger");
        const columnsPanel = document.getElementById("warehouseColumnSettingsPanel");

        function assert(condition, message) {
            if (!condition) {
                throw new Error(message);
            }
        }

        try {
            assert(root && toggle && search && columns && columnsPanel, "contract");
            const initialQuery = search.value;
            const tableHeader = document.querySelector(
                "#warehouseProductsTable thead th"
            );
            const pagination = document.querySelector("[data-erp-pagination]");

            toggle.click();
            assert(root.classList.contains("erp-focus-mode"), "enter");
            assert(toggle.getAttribute("aria-expanded") === "true", "expanded");
            assert(toggle.getAttribute("aria-label").includes("Свернуть"), "label");
            assert(document.getElementById("appSidebar").hidden, "sidebar");
            assert(document.querySelector(".products-workspace-metrics").hidden, "metrics");
            [
                "warehouseSearchInput",
                "warehouseFilterTrigger",
                "warehouseColumnSettingsTrigger",
                "warehouseProductsTable",
                "erpPagination",
            ].forEach(function (id) {
                assert(
                    document.getElementById(id).getClientRects().length > 0,
                    "visible-" + id
                );
            });
            assert(
                document.querySelector(".warehouse-availability-segments")
                    .getClientRects().length > 0
                || document.querySelector(".warehouse-availability-select")
                    .getClientRects().length > 0,
                "availability"
            );
            [
                "filterBrandComboboxTrigger",
                "filterCategoryComboboxTrigger",
                "filterModelComboboxTrigger",
            ].forEach(function (id) {
                assert(document.getElementById(id), "cascade-" + id);
            });
            assert(getComputedStyle(document.body).overflowY === "hidden", "body-scroll");
            assert(
                getComputedStyle(document.documentElement).overflowY === "hidden",
                "root-scroll"
            );
            assert(getComputedStyle(tableHeader).position === "sticky", "sticky-header");
            assert(
                pagination.getBoundingClientRect().bottom <= window.innerHeight + 1,
                "pagination-bottom"
            );
            assert(
                document.documentElement.scrollWidth
                    <= document.documentElement.clientWidth + 1,
                "horizontal-overflow"
            );

            columns.click();
            const optionalColumn = columnsPanel.querySelector(
                'input:not([disabled])'
            );
            assert(optionalColumn && !columnsPanel.hidden, "columns-open");
            optionalColumn.click();
            const savedColumnState = optionalColumn.checked;
            document.dispatchEvent(new KeyboardEvent(
                "keydown",
                {key: "Escape", bubbles: true}
            ));
            assert(columnsPanel.hidden, "columns-escape");
            assert(root.classList.contains("erp-focus-mode"), "overlay-escape");

            document.dispatchEvent(new KeyboardEvent(
                "keydown",
                {key: "Escape", bubbles: true}
            ));
            assert(!root.classList.contains("erp-focus-mode"), "collapse-escape");
            assert(search.value === initialQuery, "search-state");
            assert(optionalColumn.checked === savedColumnState, "column-state");
            assert(!document.getElementById("appSidebar").hidden, "sidebar-restore");
            assert(
                getComputedStyle(document.documentElement).overflowY !== "hidden",
                "scroll-restore"
            );

            toggle.click();
            toggle.click();
            assert(!root.classList.contains("erp-focus-mode"), "collapse-button");
            toggle.click();
            window.dispatchEvent(new Event("pagehide"));
            assert(!root.classList.contains("erp-focus-mode"), "pagehide-reset");

            documentRoot.dataset.productsFocusModeE2e = "pass";
        } catch (error) {
            documentRoot.dataset.productsFocusModeE2e = "fail";
            documentRoot.dataset.productsFocusModeE2eError = error.message;
        }
    });
})();
