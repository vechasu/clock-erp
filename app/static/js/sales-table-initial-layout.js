(function (root) {
    "use strict";

    const recommendedOrder = [
        "created_at", "order_number", "source", "product_name", "article",
        "brand", "category", "quantity_display", "unit_price_display",
        "commission", "order_status_label", "delivery_cost_display",
        "track_number", "barcode", "sticker_number", "recipient_name",
        "platform", "country", "invoice_number", "region", "city",
        "payment_method", "note",
    ];
    const pinnedColumns = ["created_at", "order_number"];
    const defaultWidths = {
        created_at: 108, order_number: 132, track_number: 124, barcode: 128,
        source: 104, brand: 104, category: 132, product_name: 300,
        article: 144, quantity_display: 96, unit_price_display: 124,
        commission: 260, order_status_label: 118,
        delivery_cost_display: 128, region: 132, city: 132,
        payment_method: 160, note: 220, sticker_number: 132,
        recipient_name: 170, platform: 150, country: 120,
        invoice_number: 140,
    };
    const legacyDefaultWidths = {
        created_at: 112, order_number: 150, track_number: 150, barcode: 150,
        source: 108, brand: 108, category: 148, product_name: 280,
        article: 150, quantity_display: 104, unit_price_display: 138,
        commission: 260, order_status_label: 130,
        delivery_cost_display: 138, region: 150, city: 150,
        payment_method: 170, note: 240, sticker_number: 150,
        recipient_name: 190, platform: 180, country: 140,
        invoice_number: 160,
    };
    const minimumWidths = {
        created_at: 108, order_number: 118, track_number: 108, barcode: 112,
        source: 90, brand: 90, category: 108, product_name: 220,
        article: 108, quantity_display: 90, unit_price_display: 112,
        commission: 220, order_status_label: 104,
        delivery_cost_display: 112, region: 100, city: 100,
        payment_method: 130, note: 160, sticker_number: 112,
        recipient_name: 140, platform: 120, country: 100,
        invoice_number: 120,
    };
    const growWeights = {
        created_at: 0.25, order_number: 0.75, track_number: 3, barcode: 1.5,
        source: 0.35, brand: 2, category: 2.5, product_name: 5,
        article: 2, quantity_display: 0.2, unit_price_display: 0.25,
        commission: 2, order_status_label: 0.5,
        delivery_cost_display: 0.25, region: 1.5, city: 1.5,
        payment_method: 1, note: 4, sticker_number: 1,
        recipient_name: 2, platform: 1.5, country: 1,
        invoice_number: 1,
    };
    const settingsVersion = 6;
    const minimumWidth = 76;
    const maximumWidth = 520;
    const actionWidth = 116;

    function buildDefaultOrder(availableColumns) {
        return [
            ...recommendedOrder.filter((key) => availableColumns.includes(key)),
            ...availableColumns.filter((key) => !recommendedOrder.includes(key)),
        ];
    }

    function normalizeView(value, availableColumns) {
        const defaultOrder = buildDefaultOrder(availableColumns);
        const source = value && typeof value === "object" ? value : {};
        const order = [];
        const sourceOrder = Array.isArray(source.order) ? source.order : [];
        const sourceHasArticle = sourceOrder.includes("article");

        sourceOrder.forEach((key) => {
            if (defaultOrder.includes(key) && !order.includes(key)) {
                order.push(key);
            }
        });
        defaultOrder.forEach((key) => {
            if (!order.includes(key)) order.push(key);
        });
        if (!sourceHasArticle && order.includes("article")) {
            order.splice(order.indexOf("article"), 1);
            order.splice(order.indexOf("product_name") + 1, 0, "article");
        }
        const normalizedOrder = [
            ...pinnedColumns.filter((key) => order.includes(key)),
            ...order.filter((key) => !pinnedColumns.includes(key)),
        ];
        let hidden = Array.isArray(source.hidden)
            ? source.hidden.filter((key, index, values) => (
                defaultOrder.includes(key) && values.indexOf(key) === index
            ))
            : [];
        if (hidden.length >= normalizedOrder.length) {
            hidden = hidden.filter((key) => key !== normalizedOrder[0]);
        }

        const sourceWidths = source.widths && typeof source.widths === "object"
            ? {...source.widths}
            : {};
        if (source.version !== settingsVersion) {
            Object.entries(legacyDefaultWidths).forEach(([key, legacyWidth]) => {
                if (
                    !Object.hasOwn(sourceWidths, key)
                    || Number(sourceWidths[key]) === legacyWidth
                ) {
                    sourceWidths[key] = defaultWidths[key];
                }
            });
        }
        const widths = {};
        defaultOrder.forEach((key) => {
            const numeric = Number(sourceWidths[key]);
            widths[key] = Math.min(
                maximumWidth,
                Math.max(
                    minimumWidths[key] || minimumWidth,
                    Number.isFinite(numeric)
                        ? Math.round(numeric)
                        : (defaultWidths[key] || 130),
                ),
            );
        });
        const suppliedCustomWidths = Array.isArray(source.customWidths)
            ? source.customWidths
            : null;
        const customWidths = (suppliedCustomWidths || Object.keys(
            sourceWidths,
        ).filter((key) => (
            defaultOrder.includes(key)
            && Number.isFinite(Number(sourceWidths[key]))
            && Number(sourceWidths[key]) !== (defaultWidths[key] || 130)
        ))).filter((key, index, values) => (
            defaultOrder.includes(key) && values.indexOf(key) === index
        ));

        return {
            version: settingsVersion,
            order: normalizedOrder,
            hidden,
            widths,
            customWidths,
        };
    }

    function readView(settingsKey, availableColumns) {
        try {
            return normalizeView(
                JSON.parse(root.localStorage.getItem(settingsKey) || "{}"),
                availableColumns,
            );
        } catch (error) {
            return normalizeView({}, availableColumns);
        }
    }

    function applyInitialLayout(table, definitions) {
        if (!table || !root.ErpTableLayout) return null;
        const availableColumns = definitions.map((column) => column.key);
        const view = readView(table.dataset.salesSettingsKey, availableColumns);
        const tableWrap = table.closest(".table-wrap");

        table.querySelectorAll("tr").forEach((row) => {
            view.order.forEach((key) => {
                const cell = row.querySelector('[data-column-key="' + key + '"]');
                if (cell) row.appendChild(cell);
            });
            const actions = row.querySelector('[data-system-column="actions"]');
            if (actions) row.appendChild(actions);
        });

        const colgroup = table.querySelector("colgroup");
        if (colgroup) {
            view.order.forEach((key) => {
                const column = colgroup.querySelector(
                    '[data-column-key="' + key + '"]',
                );
                if (column) colgroup.appendChild(column);
            });
            const actions = colgroup.querySelector(
                '[data-system-column="actions"]',
            );
            if (actions) colgroup.appendChild(actions);
        }

        const visibleKeys = view.order.filter((key) => !view.hidden.includes(key));
        const layout = root.ErpTableLayout.computeColumnWidths({
            keys: visibleKeys,
            preferredWidths: view.widths,
            minimumWidths,
            customWidths: view.customWidths,
            growWeights,
            containerWidth: tableWrap ? tableWrap.clientWidth : 0,
            actionWidth,
        });

        view.order.forEach((key) => {
            const hidden = view.hidden.includes(key);
            table.querySelectorAll('[data-column-key="' + key + '"]')
                .forEach((element) => {
                    element.hidden = hidden;
                    if (element.tagName === "COL") {
                        element.style.width = (
                            hidden ? view.widths[key] : layout.widths[key]
                        ) + "px";
                    }
                });
        });
        const actions = table.querySelector('col[data-system-column="actions"]');
        if (actions) actions.style.width = actionWidth + "px";
        table.style.width = layout.tableWidth + "px";
        table.dataset.horizontalOverflow = String(layout.overflow);
        table.dataset.initialLayoutReady = "true";
        const scrollbar = document.getElementById("salesHorizontalScrollbar");
        if (scrollbar) scrollbar.hidden = !layout.overflow;

        const visibleCount = Math.max(1, visibleKeys.length);
        table.querySelectorAll(
            ".sales-filter-empty-cell, .sales-initial-empty-row td",
        ).forEach((cell) => {
            cell.colSpan = visibleCount + 1;
        });
        return view;
    }

    root.SalesTableInitialLayout = {apply: applyInitialLayout};
})(typeof globalThis === "object" ? globalThis : this);
