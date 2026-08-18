(function (root, factory) {
    "use strict";

    const api = factory();
    if (typeof module === "object" && module.exports) {
        module.exports = api;
    }
    root.ErpTableLayout = api;
})(typeof globalThis === "object" ? globalThis : this, function () {
    "use strict";

    function finiteWidth(value, fallback) {
        const numeric = Number(value);
        return Number.isFinite(numeric) && numeric > 0
            ? numeric
            : fallback;
    }

    function sumWidths(keys, widths) {
        return keys.reduce(function (total, key) {
            return total + widths[key];
        }, 0);
    }

    function fitTotal(keys, widths, expected, adjustableKeys) {
        const actual = sumWidths(keys, widths);
        const correction = expected - actual;
        const target = adjustableKeys[adjustableKeys.length - 1]
            || keys[keys.length - 1];
        if (target && Math.abs(correction) > 0.0001) {
            widths[target] += correction;
        }
    }

    function computeColumnWidths(options) {
        const keys = Array.isArray(options.keys) ? options.keys : [];
        const preferredSource = options.preferredWidths || {};
        const minimumSource = options.minimumWidths || {};
        const weights = options.growWeights || {};
        const custom = new Set(options.customWidths || []);
        const containerWidth = Math.max(
            0,
            finiteWidth(options.containerWidth, 0)
        );
        const actionWidth = Math.max(
            0,
            finiteWidth(options.actionWidth, 0)
        );
        const contentWidth = Math.max(0, containerWidth - actionWidth);
        const preferred = {};
        const minimum = {};

        keys.forEach(function (key) {
            minimum[key] = Math.max(1, finiteWidth(minimumSource[key], 1));
            preferred[key] = Math.max(
                minimum[key],
                finiteWidth(preferredSource[key], minimum[key])
            );
        });

        const preferredTotal = sumWidths(keys, preferred);
        const minimumTotal = sumWidths(keys, minimum);
        let widths = Object.assign({}, preferred);

        if (contentWidth >= preferredTotal && keys.length) {
            const extra = contentWidth - preferredTotal;
            let adjustable = keys.filter(function (key) {
                return finiteWidth(weights[key], 0) > 0;
            });
            if (!adjustable.length) {
                adjustable = keys.slice();
            }
            const totalWeight = adjustable.reduce(function (total, key) {
                return total + finiteWidth(weights[key], 1);
            }, 0);
            adjustable.forEach(function (key) {
                widths[key] += extra
                    * finiteWidth(weights[key], 1)
                    / totalWeight;
            });
            fitTotal(keys, widths, contentWidth, adjustable);
        } else if (contentWidth >= minimumTotal && keys.length) {
            const reducibleTotal = preferredTotal - minimumTotal;
            const reduction = preferredTotal - contentWidth;
            keys.forEach(function (key) {
                const reducible = preferred[key] - minimum[key];
                widths[key] = reducibleTotal > 0
                    ? preferred[key] - reduction * reducible / reducibleTotal
                    : minimum[key];
            });
            fitTotal(keys, widths, contentWidth, keys);
        } else if (keys.length) {
            widths = {};
            keys.forEach(function (key) {
                // Defaults can collapse to their safe minimum on narrow screens.
                // Explicit user widths remain visible through horizontal scroll.
                widths[key] = custom.has(key)
                    ? preferred[key]
                    : minimum[key];
            });
        }

        const columnsWidth = sumWidths(keys, widths);
        const tableWidth = Math.max(
            columnsWidth + actionWidth,
            containerWidth
        );

        return {
            widths: widths,
            tableWidth: tableWidth,
            overflow: tableWidth > containerWidth + 0.5,
            minimumTotal: minimumTotal + actionWidth,
            preferredTotal: preferredTotal + actionWidth,
        };
    }

    return {computeColumnWidths: computeColumnWidths};
});
