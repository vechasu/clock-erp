(function () {
    "use strict";

    const phaseKey = "vechasu-sales-focus-mode-e2e-phase";
    const storageKey = "vechasu.erp.sales.focus-mode.v1";

    function fail(error) {
        document.documentElement.dataset.salesFocusModeE2eError =
            error instanceof Error ? error.message : String(error);
        sessionStorage.removeItem(phaseKey);
        try {
            localStorage.removeItem(storageKey);
        } catch (storageError) {
            // The failure marker remains available without storage.
        }
    }

    function assert(condition, message) {
        if (!condition) {
            throw new Error(message);
        }
    }

    function pressEscape() {
        document.dispatchEvent(new KeyboardEvent("keydown", {
            key: "Escape",
            bubbles: true,
            cancelable: true,
        }));
    }

    function visible(element) {
        return Boolean(element)
            && !element.hidden
            && element.getClientRects().length > 0;
    }

    function assertSparseTableFills(table, tableWrap) {
        const keep = new Set([
            "brand",
            "order_status_label",
            "track_number",
        ]);
        document.getElementById("salesColumnSettingsTrigger").click();
        window.salesColumnSettings.getView().order.forEach(function (key) {
            const checkbox = document.querySelector(
                '[data-column-visibility-key="' + key + '"]'
            );
            const shouldShow = keep.has(key);
            if (checkbox && checkbox.checked !== shouldShow) {
                checkbox.click();
            }
        });
        document.getElementById("salesColumnSettingsClose").click();
        window.salesColumnSettings.relayout();

        const visibleHeaders = Array.from(table.querySelectorAll(
            "thead th[data-column-key]"
        )).filter(function (header) {
            return !header.hidden;
        });
        const actionHeader = table.querySelector(
            'thead [data-system-column="actions"]'
        );
        const firstRow = table.querySelector("tbody .sale-row");
        const availableKeepCount = window.salesColumnSettings.getView()
            .order.filter(function (key) {
                return keep.has(key);
            }).length;
        assert(
            availableKeepCount >= 2
                && visibleHeaders.length === availableKeepCount,
            "sparse-visible-count"
        );
        assert(
            Math.abs(table.getBoundingClientRect().width - tableWrap.clientWidth)
                <= 1,
            "sparse-card-width"
        );
        assert(
            Math.abs(
                actionHeader.getBoundingClientRect().width
                - 116
            ) <= 1,
            "sparse-action-width"
        );
        visibleHeaders.forEach(function (header) {
            const cell = firstRow.querySelector(
                '[data-column-key="' + header.dataset.columnKey + '"]'
            );
            assert(
                Math.abs(
                    header.getBoundingClientRect().width
                    - cell.getBoundingClientRect().width
                ) <= 1,
                "sparse-grid-alignment"
            );
        });
        assert(
            firstRow.querySelector(".sales-row-actions").scrollWidth
                <= actionHeader.getBoundingClientRect().width,
            "sparse-actions-nowrap"
        );
    }

    window.addEventListener("DOMContentLoaded", function () {
        window.setTimeout(function () {
            try {
                const root = document.querySelector("[data-erp-focus-mode]");
                const toggle = document.getElementById("salesFocusModeToggle");
                const toolbar = document.querySelector(".sales-data-toolbar");
                const table = document.querySelector(".sales-table");
                const tableWrap = document.querySelector(".table-wrap");
                const search = document.getElementById("salesSearch");
                const controller = root?.erpFocusModeController;
                const phase = sessionStorage.getItem(phaseKey);

                assert(root && toggle && toolbar && table && tableWrap && search,
                    "required-elements");
                assert(controller, "controller");

                if (phase === "restore") {
                    assert(controller.isEnabled(), "restore-state");
                    assert(root.classList.contains("erp-focus-mode"), "restore-class");
                    assert(toggle.getAttribute("aria-pressed") === "true",
                        "restore-aria");
                    assert(new URL(location.href).searchParams.get("source")
                        === table.dataset.source, "restore-source");
                    assert(document.activeElement !== toggle, "restore-focus");
                    sessionStorage.removeItem(phaseKey);
                    localStorage.removeItem(storageKey);
                    controller.apply(false, {persist: false});
                    document.documentElement.dataset.salesFocusModeE2e = "pass";
                    return;
                }

                localStorage.removeItem(storageKey);
                controller.apply(false, {persist: false});
                const initialUrl = location.href;
                const initialTable = table;
                const initialWidth = table.querySelector("col")?.style.width || "";
                search.value = "focus-check";

                toggle.click();
                assert(controller.isEnabled(), "enter");
                assert(toggle.getAttribute("aria-pressed") === "true", "enter-aria");
                assert(toggle.textContent.includes("Свернуть"), "enter-label");
                assert(document.getElementById("appSidebar").hidden, "sidebar-hidden");
                assert(document.querySelector(".sales-page-header").hidden,
                    "header-hidden");
                assert(document.querySelector(".sales-tabs-scroll").hidden,
                    "tabs-hidden");
                assert(document.querySelector(".erp-workspace-metrics").hidden,
                    "kpis-hidden");
                assert(location.href === initialUrl, "url-preserved");
                assert(search.value === "focus-check", "search-preserved");
                assert(document.querySelector(".sales-table") === initialTable,
                    "table-reinitialized");
                assert((table.querySelector("col")?.style.width || "") === initialWidth,
                    "column-width-reset");
                assert(visible(tableWrap), "table-visible");
                assert(getComputedStyle(tableWrap).overflowY === "auto",
                    "vertical-scroll-owner");
                assert(document.body.scrollHeight <= window.innerHeight + 1,
                    "double-scroll");

                const toolbarBounds = toolbar.getBoundingClientRect();
                const tableBounds = tableWrap.getBoundingClientRect();
                const headerBounds = table.querySelector("th")
                    ?.getBoundingClientRect();
                assert(tableBounds.top >= toolbarBounds.bottom - 1,
                    "toolbar-overlap");
                assert(headerBounds && headerBounds.top >= tableBounds.top - 1,
                    "header-overlap");

                const filterTrigger = document.getElementById("salesFilterTrigger");
                filterTrigger.click();
                const filterPanel = document.getElementById("salesFilterPanel");
                assert(visible(filterPanel), "filter-open");
                const popoverRoot = filterPanel.parentElement;
                assert(Number.parseInt(getComputedStyle(popoverRoot).zIndex, 10)
                    > Number.parseInt(getComputedStyle(toolbar).zIndex, 10),
                    "overlay-z-index");
                pressEscape();
                assert(!visible(filterPanel), "filter-escape");
                assert(controller.isEnabled(), "filter-escape-focus");

                document.getElementById("salesColumnSettingsTrigger").click();
                const columnPanel = document.getElementById(
                    "salesColumnSettingsPanel"
                );
                assert(visible(columnPanel), "columns-open");
                pressEscape();
                assert(!visible(columnPanel), "columns-escape");
                assert(controller.isEnabled(), "columns-escape-focus");

                assertSparseTableFills(table, tableWrap);

                pressEscape();
                assert(!controller.isEnabled(), "exit-escape");
                requestAnimationFrame(function () {
                    try {
                        window.salesColumnSettings.relayout();
                        assert(
                            Math.abs(
                                table.getBoundingClientRect().width
                                - tableWrap.clientWidth
                            ) <= 1,
                            "sparse-exit-width"
                        );
                        assert(document.activeElement === toggle, "exit-focus");
                        assert(!document.getElementById("appSidebar").hidden,
                            "sidebar-restored");
                        assert(search.value === "focus-check", "exit-search");
                        for (let index = 0; index < 3; index += 1) {
                            toggle.click();
                            window.salesColumnSettings.relayout();
                            assert(
                                Math.abs(
                                    table.getBoundingClientRect().width
                                    - tableWrap.clientWidth
                                ) <= 1,
                                "repeat-enter-width"
                            );
                            toggle.click();
                            window.salesColumnSettings.relayout();
                        }
                        assert(document.querySelectorAll("#salesFocusModeToggle").length
                            === 1, "duplicate-toggle");
                        assert(document.querySelector(".sales-table") === initialTable,
                            "duplicate-table");
                        toggle.click();
                        sessionStorage.setItem(phaseKey, "restore");
                        location.reload();
                    } catch (error) {
                        fail(error);
                    }
                });
            } catch (error) {
                fail(error);
            }
        }, 250);
    });
})();
