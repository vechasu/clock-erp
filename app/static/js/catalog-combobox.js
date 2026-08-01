(function initializeCatalogComboboxComponent() {
    function getComboboxTrigger(combobox) {
        return combobox
            ? combobox.querySelector(".brand-combobox-trigger")
            : null;
    }

    window.setBrandDropdownOpen = function(combobox, isOpen) {
        if (!combobox) {
            return;
        }

        const trigger = combobox.querySelector(
            ".brand-combobox-trigger"
        );

        if (isOpen && trigger && trigger.disabled) {
            return;
        }

        if (isOpen) {
            document
                .querySelectorAll("[data-brand-combobox].open")
                .forEach(function(openCombobox) {
                    if (openCombobox !== combobox) {
                        openCombobox.classList.remove("open");
                        const openTrigger =
                            getComboboxTrigger(openCombobox);

                        if (openTrigger) {
                            openTrigger.setAttribute(
                                "aria-expanded",
                                "false"
                            );
                        }
                    }
                });
        }

        combobox.classList.toggle("open", isOpen);

        if (trigger) {
            trigger.setAttribute(
                "aria-expanded",
                isOpen ? "true" : "false"
            );
        }

        if (
            isOpen
            && combobox.dataset.searchEnabled !== "false"
        ) {
            const search = combobox.querySelector(
                ".brand-combobox-search"
            );

            window.setTimeout(function() {
                if (search) {
                    search.focus();
                }
            }, 20);
        }
    };

    window.toggleBrandDropdown = function(event, combobox) {
        if (event) {
            event.stopPropagation();
        }

        if (!combobox) {
            return;
        }

        window.setBrandDropdownOpen(
            combobox,
            !combobox.classList.contains("open")
        );
    };

    window.normalizeComboboxSearchValue = function(value) {
        return String(value || "")
            .trim()
            .toLocaleLowerCase("ru")
            .replaceAll("ё", "е");
    };

    window.filterBrandList = function(value, combobox) {
        const query = window.normalizeComboboxSearchValue(value);
        let visibleCount = 0;

        combobox
            .querySelectorAll(".brand-combobox-option")
            .forEach(function(option) {
                option.classList.remove("is-keyboard-active");
                const label = option.querySelector(
                    ".brand-combobox-option-label"
                );
                const searchValue =
                    option.dataset.brandSearch
                    || (label ? label.textContent : "");
                const normalized =
                    window.normalizeComboboxSearchValue(searchValue);
                const matches =
                    combobox.dataset.prefixSearch === "true"
                        ? normalized.startsWith(query)
                        : normalized.includes(query);
                const hidden = Boolean(query) && !matches;
                option.hidden = hidden;

                if (!hidden) {
                    visibleCount += 1;
                }
            });

        const clearButton = combobox.querySelector(
            "[data-brand-search-clear]"
        );
        const emptyMessage = combobox.querySelector(
            "[data-brand-empty]"
        );

        if (clearButton) {
            clearButton.hidden = !query;
        }

        if (emptyMessage) {
            emptyMessage.hidden = visibleCount !== 0;
        }
    };

    window.setBrandComboboxValue = function(
        combobox,
        value,
        displayValue
    ) {
        if (!combobox) {
            return;
        }

        const hiddenInput = combobox.querySelector(
            ".brand-combobox-hidden"
        );
        const valueElement = combobox.querySelector(
            ".brand-combobox-value"
        );
        const emptyLabel =
            combobox.dataset.allLabel || "Выберите значение";
        const previousValue = hiddenInput
            ? hiddenInput.value
            : "";

        if (hiddenInput) {
            hiddenInput.value = value;
        }

        if (valueElement) {
            valueElement.textContent =
                displayValue || value || emptyLabel;
        }

        const bulkField = combobox.closest(
            ".warehouse-bulk-field"
        );
        const autoApplyToggle = bulkField
            ? bulkField.querySelector(
                'input[data-bulk-auto-apply="true"]'
            )
            : null;

        if (autoApplyToggle) {
            autoApplyToggle.checked = Boolean(value);
        }

        combobox
            .querySelectorAll(".brand-combobox-option")
            .forEach(function(option) {
                option.classList.toggle(
                    "active",
                    (option.dataset.brand || "") === value
                );
            });

        if (
            autoApplyToggle
            && typeof window.syncWarehouseBulkSelection
                === "function"
        ) {
            window.syncWarehouseBulkSelection();
        }

        if (previousValue !== value) {
            combobox.dispatchEvent(
                new CustomEvent(
                    "catalog-combobox:change",
                    {
                        bubbles: true,
                        detail: {
                            value: value,
                            displayValue:
                                displayValue || value || "",
                        },
                    }
                )
            );
        }
    };

    window.setCatalogComboboxDisabled = function(
        combobox,
        disabled,
        placeholder
    ) {
        if (!combobox) {
            return;
        }

        const trigger = combobox.querySelector(
            ".brand-combobox-trigger"
        );
        const hiddenInput = combobox.querySelector(
            ".brand-combobox-hidden"
        );
        const idInput = combobox.querySelector(
            ".catalog-combobox-id"
        );
        const valueElement = combobox.querySelector(
            ".brand-combobox-value"
        );

        if (trigger) {
            trigger.disabled = Boolean(disabled);
        }

        if (hiddenInput) {
            hiddenInput.disabled = Boolean(disabled);
        }
        if (idInput) {
            idInput.disabled = Boolean(disabled);
        }

        if (placeholder) {
            combobox.dataset.allLabel = placeholder;

            if (!hiddenInput || !hiddenInput.value) {
                valueElement.textContent = placeholder;
            }
        }

        if (disabled) {
            window.setBrandDropdownOpen(combobox, false);
        }
    };

    window.replaceCatalogComboboxOptions = function(
        combobox,
        options,
        emptyLabel
    ) {
        const optionsElement = combobox.querySelector(
            ".brand-combobox-options"
        );
        const emptyMessage = combobox.querySelector(
            "[data-brand-empty]"
        );

        optionsElement
            .querySelectorAll(".brand-combobox-option")
            .forEach(function(option) {
                option.remove();
            });

        (Array.isArray(options) ? options : []).forEach(
            function(option) {
                const button = document.createElement("button");
                button.className = "brand-combobox-option";
                button.type = "button";
                button.setAttribute("role", "option");
                button.dataset.brand = option.value || "";
                button.dataset.brandSearch =
                    option.searchText || option.label || "";
                if (option.item) {
                    button.dataset.catalogItem = JSON.stringify(
                        option.item
                    );
                }

                const count = document.createElement("span");
                count.className = "catalog-combobox-option-count";
                count.textContent =
                    option.count === undefined
                    || option.count === null
                        ? ""
                        : option.count;

                const label = document.createElement("span");
                label.className = "brand-combobox-option-label";
                label.textContent = option.label || option.value || "";

                if (option.meta) {
                    const copy = document.createElement("span");
                    const meta = document.createElement("span");
                    copy.className = "catalog-combobox-option-copy";
                    meta.className = "catalog-combobox-option-meta";
                    meta.textContent = option.meta;
                    copy.append(label, meta);
                    button.append(copy, count);
                } else {
                    button.append(label, count);
                }
                optionsElement.insertBefore(button, emptyMessage);
            }
        );

        if (emptyMessage && emptyLabel) {
            emptyMessage.textContent = emptyLabel;
        }

        window.filterBrandList("", combobox);
    };

    window.selectBrandOption = function(button) {
        const sharedItem = button.dataset.catalogItem
            ? JSON.parse(button.dataset.catalogItem)
            : null;
        const value = button.dataset.brand || "";
        const combobox = button.closest("[data-brand-combobox]");
        const label = button.querySelector(
            ".brand-combobox-option-label"
        );
        const displayValue = label
            ? label.textContent.trim()
            : value;

        if (
            sharedItem
            && typeof window.setSharedCatalogComboboxValue
                === "function"
        ) {
            window.setSharedCatalogComboboxValue(
                combobox,
                sharedItem
            );
        } else {
            window.setBrandComboboxValue(
                combobox,
                value,
                displayValue
            );
        }

        const searchInput = combobox.querySelector(
            ".brand-combobox-search"
        );

        if (searchInput) {
            searchInput.value = "";
        }

        window.filterBrandList("", combobox);
        window.setBrandDropdownOpen(combobox, false);
    };

    window.initializeBrandComboboxes = function(root) {
        (root || document)
            .querySelectorAll("[data-brand-combobox]")
            .forEach(function(combobox) {
                const trigger = getComboboxTrigger(combobox);
                const searchInput = combobox.querySelector(
                    "[data-brand-search-input]"
                );
                const clearButton = combobox.querySelector(
                    "[data-brand-search-clear]"
                );

                if (
                    !trigger
                    || !searchInput
                    || searchInput.dataset.searchBound === "1"
                ) {
                    return;
                }

                searchInput.dataset.searchBound = "1";
                trigger.addEventListener("click", function(event) {
                    window.toggleBrandDropdown(event, combobox);

                    if (
                        combobox.classList.contains("open")
                        && typeof window.loadSharedCatalogOptions
                            === "function"
                    ) {
                        window.loadSharedCatalogOptions(
                            combobox,
                            searchInput.value
                        );
                    }
                });
                searchInput.addEventListener("input", function() {
                    if (
                        combobox.dataset
                            .clearSelectionOnSearchClear === "true"
                    ) {
                        if (
                            combobox.dataset.sharedCatalogKind
                            && typeof window
                                .clearSharedCatalogCombobox === "function"
                        ) {
                            window.clearSharedCatalogCombobox(
                                combobox
                            );
                        } else {
                            window.setBrandComboboxValue(
                                combobox,
                                ""
                            );
                        }
                    }

                    if (
                        typeof window.queueSharedCatalogSearch
                            === "function"
                        && combobox.dataset.sharedCatalogKind
                    ) {
                        window.queueSharedCatalogSearch(
                            combobox,
                            searchInput.value
                        );
                    } else {
                        window.filterBrandList(
                            searchInput.value,
                            combobox
                        );
                    }
                });

                if (clearButton) {
                    clearButton.addEventListener(
                        "click",
                        function() {
                            searchInput.value = "";

                            if (
                                combobox.dataset
                                    .clearSelectionOnSearchClear
                                    === "true"
                            ) {
                                if (
                                    combobox.dataset.sharedCatalogKind
                                    && typeof window
                                        .clearSharedCatalogCombobox
                                        === "function"
                                ) {
                                    window.clearSharedCatalogCombobox(
                                        combobox
                                    );
                                } else {
                                    window.setBrandComboboxValue(
                                        combobox,
                                        ""
                                    );
                                }
                            }

                            if (
                                typeof window.loadSharedCatalogOptions
                                    === "function"
                                && combobox.dataset.sharedCatalogKind
                            ) {
                                window.loadSharedCatalogOptions(
                                    combobox,
                                    ""
                                );
                            } else {
                                window.filterBrandList("", combobox);
                            }
                            searchInput.focus();
                        }
                    );
                }

                searchInput.addEventListener(
                    "keydown",
                    function(event) {
                        const visibleOptions = Array.from(
                            combobox.querySelectorAll(
                                ".brand-combobox-option"
                                + ":not([hidden])"
                            )
                        );
                        const currentIndex =
                            visibleOptions.findIndex(
                                function(option) {
                                    return option.classList.contains(
                                        "is-keyboard-active"
                                    );
                                }
                            );

                        if (
                            event.key === "ArrowDown"
                            || event.key === "ArrowUp"
                        ) {
                            event.preventDefault();
                            const direction =
                                event.key === "ArrowDown" ? 1 : -1;
                            let nextIndex = currentIndex + direction;

                            if (currentIndex < 0) {
                                nextIndex = direction > 0
                                    ? 0
                                    : visibleOptions.length - 1;
                            }

                            if (visibleOptions.length) {
                                nextIndex = (
                                    nextIndex
                                    + visibleOptions.length
                                ) % visibleOptions.length;
                                visibleOptions.forEach(
                                    function(option) {
                                        option.classList.remove(
                                            "is-keyboard-active"
                                        );
                                    }
                                );
                                visibleOptions[nextIndex]
                                    .classList.add(
                                        "is-keyboard-active"
                                    );
                                visibleOptions[nextIndex]
                                    .scrollIntoView({
                                        block: "nearest",
                                    });
                            }
                        } else if (event.key === "Enter") {
                            const activeOption =
                                visibleOptions[currentIndex];

                            if (activeOption) {
                                event.preventDefault();
                                window.selectBrandOption(
                                    activeOption
                                );
                            }
                        } else if (event.key === "Escape") {
                            event.preventDefault();
                            event.stopPropagation();
                            window.setBrandDropdownOpen(
                                combobox,
                                false
                            );
                            trigger.focus();
                        }
                    }
                );
                window.filterBrandList(
                    searchInput.value,
                    combobox
                );
            });
    };

    document.addEventListener("click", function(event) {
        const option = event.target.closest(
            ".brand-combobox-option"
        );

        if (option) {
            window.selectBrandOption(option);
            return;
        }

        document
            .querySelectorAll("[data-brand-combobox]")
            .forEach(function(combobox) {
                if (!combobox.contains(event.target)) {
                    window.setBrandDropdownOpen(
                        combobox,
                        false
                    );
                }
            });
    });

    document.addEventListener("DOMContentLoaded", function() {
        window.initializeBrandComboboxes(document);
    });
})();

(function initializeSharedCatalogCascades() {
    const searchTimers = new WeakMap();
    const requestControllers = new WeakMap();

    function sharedCatalogScope(combobox) {
        return combobox?.closest("[data-shared-catalog-scope]");
    }

    function sharedCatalogCombobox(scope, kind) {
        return scope?.querySelector(
            '[data-shared-catalog-kind="' + kind + '"]'
        );
    }

    function sharedCatalogPrimaryInput(combobox) {
        return combobox?.querySelector(".brand-combobox-hidden");
    }

    function sharedCatalogIdInput(combobox) {
        return (
            combobox?.querySelector(".catalog-combobox-id")
            || (
                combobox?.dataset.sharedCatalogKind === "product"
                    ? sharedCatalogPrimaryInput(combobox)
                    : null
            )
        );
    }

    function selectedSharedCatalogId(combobox) {
        return String(
            sharedCatalogIdInput(combobox)?.value || ""
        );
    }

    function sharedCatalogProductLabel(item) {
        const details = [
            item.article || "без артикула",
            "остаток " + (item.stock_display ?? item.stock ?? 0),
        ];
        return [item.name || "Товар без названия", ...details]
            .join(" · ");
    }

    function sharedCatalogOption(item, kind) {
        const product = kind === "product";
        return {
            value: product
                ? String(item.id || "")
                : String(item.name || ""),
            label: product
                ? sharedCatalogProductLabel(item)
                : String(item.name || ""),
            searchText: [
                item.name,
                item.article,
                item.barcode,
            ].filter(Boolean).join(" "),
            meta: product
                ? [
                    item.article
                        ? "Артикул: " + item.article
                        : "",
                    item.barcode
                        ? "Баркод: " + item.barcode
                        : "",
                ].filter(Boolean).join(" · ")
                : "",
            count: product
                ? "Остаток: " + (item.stock_display ?? item.stock ?? 0)
                : (item.product_count ?? item.count ?? ""),
            item,
        };
    }

    window.clearSharedCatalogCombobox = function(combobox) {
        if (!combobox) {
            return;
        }

        const idInput = sharedCatalogIdInput(combobox);

        if (idInput) {
            idInput.value = "";
        }

        combobox.dataset.sharedCatalogSelectedId = "";
        combobox.dataset.sharedCatalogSelectedLabel = "";
        window.setBrandComboboxValue(combobox, "", "");
    };

    window.setSharedCatalogComboboxValue = function(
        combobox,
        item
    ) {
        if (!combobox || !item) {
            return;
        }

        const kind = combobox.dataset.sharedCatalogKind;
        const itemId = String(item.id || "");
        const primaryValue = kind === "product"
            ? itemId
            : String(item.name || "");
        const displayValue = kind === "product"
            ? sharedCatalogProductLabel(item)
            : String(item.name || "");
        const idInput = sharedCatalogIdInput(combobox);

        if (idInput) {
            idInput.value = itemId;
        }

        combobox.dataset.sharedCatalogSelectedId = itemId;
        combobox.dataset.sharedCatalogSelectedLabel = displayValue;
        window.setBrandComboboxValue(
            combobox,
            primaryValue,
            displayValue
        );
        combobox.dispatchEvent(
            new CustomEvent("shared-catalog:selected", {
                bubbles: true,
                detail: {
                    kind,
                    id: itemId,
                    label: displayValue,
                    item,
                },
            })
        );
    };

    function setSharedCatalogLoading(combobox, loading) {
        const emptyMessage = combobox?.querySelector(
            "[data-brand-empty]"
        );

        combobox?.classList.toggle("is-loading", loading);

        if (emptyMessage && loading) {
            emptyMessage.textContent = "Загрузка…";
            emptyMessage.hidden = false;
        }
    }

    function clearSharedCatalogOptions(combobox, emptyLabel) {
        if (!combobox) {
            return;
        }

        window.replaceCatalogComboboxOptions(
            combobox,
            [],
            emptyLabel || "Ничего не найдено"
        );
    }

    window.loadSharedCatalogOptions = async function(
        combobox,
        rawQuery
    ) {
        if (!combobox?.dataset.sharedCatalogKind) {
            return [];
        }

        const trigger = combobox.querySelector(
            ".brand-combobox-trigger"
        );

        if (trigger?.disabled) {
            return [];
        }

        const scope = sharedCatalogScope(combobox);
        const kind = combobox.dataset.sharedCatalogKind;
        const brandId = selectedSharedCatalogId(
            sharedCatalogCombobox(scope, "brand")
        );
        const categoryId = selectedSharedCatalogId(
            sharedCatalogCombobox(scope, "category")
        );

        if (
            (kind === "category" && !brandId)
            || (kind === "product" && (!brandId || !categoryId))
        ) {
            clearSharedCatalogOptions(
                combobox,
                "Ничего не найдено"
            );
            return [];
        }

        requestControllers.get(combobox)?.abort();
        const controller = new AbortController();
        requestControllers.set(combobox, controller);
        const parameters = new URLSearchParams({
            type: kind,
            limit: "100",
        });
        const query = String(rawQuery || "").trim();

        if (query) {
            parameters.set("q", query);
        }
        if (kind !== "brand") {
            parameters.set("brand_id", brandId);
        }
        if (kind === "product") {
            parameters.set("category_id", categoryId);
            if (scope?.dataset.catalogInStock === "true") {
                parameters.set("in_stock", "1");
            }
        }

        setSharedCatalogLoading(combobox, true);

        try {
            const response = await fetch(
                "/api/v1/catalog/options?" + parameters.toString(),
                {
                    credentials: "same-origin",
                    signal: controller.signal,
                    headers: {
                        Accept: "application/json",
                    },
                }
            );
            const payload = await response.json();

            if (!response.ok) {
                throw new Error(
                    payload?.message
                    || payload?.error?.message
                    || "Не удалось загрузить справочник"
                );
            }

            const items = Array.isArray(payload?.data)
                ? payload.data
                : [];
            const options = items.map((item) =>
                sharedCatalogOption(item, kind)
            );
            window.replaceCatalogComboboxOptions(
                combobox,
                options,
                "Ничего не найдено"
            );
            window.filterBrandList(query, combobox);
            return items;
        } catch (error) {
            if (error.name === "AbortError") {
                return [];
            }

            clearSharedCatalogOptions(
                combobox,
                "Не удалось загрузить значения"
            );
            return [];
        } finally {
            if (requestControllers.get(combobox) === controller) {
                requestControllers.delete(combobox);
                setSharedCatalogLoading(combobox, false);
            }
        }
    };

    window.queueSharedCatalogSearch = function(
        combobox,
        query
    ) {
        window.clearTimeout(searchTimers.get(combobox));
        searchTimers.set(
            combobox,
            window.setTimeout(function() {
                window.loadSharedCatalogOptions(
                    combobox,
                    query
                );
            }, 180)
        );
    };

    function setCascadeFieldDisabled(scope, kind, disabled) {
        const combobox = sharedCatalogCombobox(scope, kind);
        const labels = {
            category: disabled
                ? "Сначала выберите бренд"
                : "Выберите категорию",
            product: disabled
                ? "Сначала выберите бренд и категорию"
                : "Выберите товар",
        };

        window.setCatalogComboboxDisabled(
            combobox,
            disabled,
            labels[kind]
        );
    }

    function resetCascadeAfter(scope, kind) {
        if (!scope || scope.dataset.catalogCascadeResetting === "1") {
            return;
        }

        scope.dataset.catalogCascadeResetting = "1";

        try {
            if (kind === "brand") {
                const category = sharedCatalogCombobox(
                    scope,
                    "category"
                );
                const product = sharedCatalogCombobox(
                    scope,
                    "product"
                );
                window.clearSharedCatalogCombobox(category);
                window.clearSharedCatalogCombobox(product);
                clearSharedCatalogOptions(
                    category,
                    "Ничего не найдено"
                );
                clearSharedCatalogOptions(
                    product,
                    "Ничего не найдено"
                );
                setCascadeFieldDisabled(
                    scope,
                    "category",
                    !selectedSharedCatalogId(
                        sharedCatalogCombobox(scope, "brand")
                    )
                );
                setCascadeFieldDisabled(scope, "product", true);
            } else if (kind === "category") {
                const product = sharedCatalogCombobox(
                    scope,
                    "product"
                );
                window.clearSharedCatalogCombobox(product);
                clearSharedCatalogOptions(
                    product,
                    "Ничего не найдено"
                );
                setCascadeFieldDisabled(
                    scope,
                    "product",
                    !selectedSharedCatalogId(
                        sharedCatalogCombobox(scope, "category")
                    )
                );
            }
        } finally {
            delete scope.dataset.catalogCascadeResetting;
        }
    }

    window.restoreSharedCatalogCascade = function(
        scope,
        values
    ) {
        if (!scope) {
            return;
        }

        scope.dataset.catalogCascadeResetting = "1";

        try {
            ["brand", "category", "product"].forEach(function(kind) {
                const combobox = sharedCatalogCombobox(scope, kind);

                if (!combobox) {
                    return;
                }

                const id = String(
                    values?.[kind + "Id"] || ""
                );
                const label = String(
                    values?.[kind + "Label"] || ""
                );
                const primary = kind === "product" ? id : label;
                const idInput = sharedCatalogIdInput(combobox);

                if (idInput) {
                    idInput.value = id;
                }

                combobox.dataset.sharedCatalogSelectedId = id;
                combobox.dataset.sharedCatalogSelectedLabel = label;
                window.setBrandComboboxValue(
                    combobox,
                    primary,
                    label
                );
            });
        } finally {
            delete scope.dataset.catalogCascadeResetting;
        }

        setCascadeFieldDisabled(
            scope,
            "category",
            !values?.brandId
        );
        setCascadeFieldDisabled(
            scope,
            "product",
            !values?.brandId || !values?.categoryId
        );
    };

    function bindSharedCatalogScope(scope) {
        if (scope.dataset.sharedCatalogBound === "1") {
            return;
        }

        scope.dataset.sharedCatalogBound = "1";
        scope.addEventListener(
            "catalog-combobox:change",
            function(event) {
                const combobox = event.target.closest(
                    "[data-shared-catalog-kind]"
                );

                if (!combobox || !scope.contains(combobox)) {
                    return;
                }

                resetCascadeAfter(
                    scope,
                    combobox.dataset.sharedCatalogKind
                );
            }
        );

        const brand = sharedCatalogCombobox(scope, "brand");
        const category = sharedCatalogCombobox(scope, "category");

        setCascadeFieldDisabled(
            scope,
            "category",
            !selectedSharedCatalogId(brand)
        );
        setCascadeFieldDisabled(
            scope,
            "product",
            !selectedSharedCatalogId(brand)
            || !selectedSharedCatalogId(category)
        );
    }

    function catalogCsrfToken(scope) {
        return (
            scope?.querySelector('input[name="csrf_token"]')?.value
            || document.querySelector(
                'input[name="csrf_token"]'
            )?.value
            || ""
        );
    }

    function initializeSharedCatalogCreation() {
        const modal = document.querySelector(
            "[data-shared-catalog-create-modal]"
        );

        if (!modal || modal.dataset.catalogCreateBound === "1") {
            return;
        }

        modal.dataset.catalogCreateBound = "1";
        const form = modal.querySelector(
            "[data-catalog-create-form]"
        );
        const title = modal.querySelector(
            "[data-catalog-create-title]"
        );
        const description = modal.querySelector(
            "[data-catalog-create-description]"
        );
        const nameInput = modal.querySelector(
            "[data-catalog-create-name]"
        );
        const articleField = modal.querySelector(
            "[data-catalog-create-article-field]"
        );
        const articleInput = modal.querySelector(
            "[data-catalog-create-article]"
        );
        const imageField = modal.querySelector(
            "[data-catalog-create-image-field]"
        );
        const imageInput = modal.querySelector(
            "[data-catalog-create-image]"
        );
        const errorElement = modal.querySelector(
            "[data-catalog-create-error]"
        );
        const submit = modal.querySelector(
            "[data-catalog-create-submit]"
        );
        let active = null;

        function readProductImage() {
            const file = imageInput?.files?.[0];

            if (!file) {
                return Promise.resolve(null);
            }
            if (!["image/jpeg", "image/png"].includes(file.type)) {
                return Promise.reject(new Error(
                    "Поддерживаются только JPEG и PNG"
                ));
            }
            if (file.size > 3 * 1024 * 1024) {
                return Promise.reject(new Error(
                    "Файл слишком большой. Максимальный размер — 3 МБ"
                ));
            }
            return new Promise((resolve, reject) => {
                const reader = new FileReader();
                reader.onload = () => resolve({
                    name: file.name,
                    type: file.type,
                    base64: String(reader.result || ""),
                });
                reader.onerror = () => reject(new Error(
                    "Не удалось прочитать изображение"
                ));
                reader.readAsDataURL(file);
            });
        }

        function closeModal() {
            modal.hidden = true;
            active?.trigger?.focus();
            active = null;
        }

        document.addEventListener("click", function(event) {
            const trigger = event.target.closest(
                "[data-catalog-create-action]"
            );

            if (!trigger) {
                return;
            }

            const combobox = trigger.closest(
                "[data-shared-catalog-kind]"
            );
            const scope = sharedCatalogScope(combobox);

            if (!combobox || !scope) {
                return;
            }

            event.preventDefault();
            const kind = trigger.dataset.catalogCreateAction;
            const titles = {
                brand: "Новый бренд",
                category: "Новая категория",
                product: "Новый товар",
            };
            active = {kind, scope, combobox, trigger};
            title.textContent = titles[kind];
            description.textContent =
                "Значение сохранится в едином справочнике Vechasu ERP.";
            articleField.hidden = kind !== "product";
            imageField.hidden = kind !== "product";
            nameInput.value = "";
            articleInput.value = "";
            imageInput.value = "";
            errorElement.textContent = "";
            modal.hidden = false;
            window.setBrandDropdownOpen(combobox, false);
            window.requestAnimationFrame(() => nameInput.focus());
        });

        form.addEventListener("submit", async function(event) {
            event.preventDefault();

            if (!active) {
                return;
            }

            const name = nameInput.value.replace(/\s+/g, " ").trim();

            if (!name) {
                errorElement.textContent = "Введите название";
                return;
            }

            const brandId = selectedSharedCatalogId(
                sharedCatalogCombobox(active.scope, "brand")
            );
            const categoryId = selectedSharedCatalogId(
                sharedCatalogCombobox(active.scope, "category")
            );
            const paths = {
                brand: "/api/v1/brands",
                category: "/api/v1/categories",
                product: "/api/v1/products",
            };
            const payload = active.kind === "brand"
                ? {name}
                : active.kind === "category"
                    ? {name, brand_id: Number(brandId)}
                    : {
                        name,
                        article: articleInput.value.trim(),
                        brand_id: Number(brandId),
                        category_id: Number(categoryId),
                        brand: "",
                        category: "",
                        cell: "",
                        stock: 0,
                    };

            submit.disabled = true;
            submit.textContent = "Создаём…";
            errorElement.textContent = "";

            try {
                if (active.kind === "product") {
                    payload.product_image = await readProductImage();
                }
                const response = await fetch(paths[active.kind], {
                    method: "POST",
                    credentials: "same-origin",
                    headers: {
                        Accept: "application/json",
                        "Content-Type": "application/json",
                        "X-CSRF-Token": catalogCsrfToken(
                            active.scope
                        ),
                    },
                    body: JSON.stringify(payload),
                });
                const result = await response.json();

                if (!response.ok) {
                    throw new Error(
                        result?.message
                        || result?.error?.message
                        || "Не удалось сохранить значение"
                    );
                }

                window.setSharedCatalogComboboxValue(
                    active.combobox,
                    result.data
                );
                closeModal();
            } catch (error) {
                errorElement.textContent =
                    error.message || "Не удалось сохранить значение";
            } finally {
                submit.disabled = false;
                submit.textContent = "Создать";
            }
        });

        modal.querySelectorAll(
            "[data-catalog-create-close],"
            + "[data-catalog-create-cancel]"
        ).forEach((button) =>
            button.addEventListener("click", closeModal)
        );
        modal.addEventListener("click", function(event) {
            if (event.target === modal) {
                closeModal();
            }
        });
    }

    window.initializeSharedCatalogCascades = function(root) {
        (root || document)
            .querySelectorAll("[data-shared-catalog-scope]")
            .forEach(bindSharedCatalogScope);
        initializeSharedCatalogCreation();
    };

    document.addEventListener("DOMContentLoaded", function() {
        window.initializeSharedCatalogCascades(document);
    });
})();
