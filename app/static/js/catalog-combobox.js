(function initializeCatalogComboboxComponent() {
    function getComboboxTrigger(combobox) {
        return combobox
            ? combobox.querySelector(".brand-combobox-trigger")
            : null;
    }

    function positionComboboxDropdown(combobox) {
        const dropdown = combobox?.querySelector(
            ".brand-combobox-dropdown"
        );
        const trigger = getComboboxTrigger(combobox);

        if (!dropdown || !trigger || !combobox.classList.contains("open")) {
            return;
        }

        const options = dropdown.querySelector(".brand-combobox-options");
        const dialog = combobox.closest(".modal-dialog, .erp-modal-dialog");
        const triggerRect = trigger.getBoundingClientRect();
        const dialogRect = dialog?.getBoundingClientRect();
        const edge = 8;
        const gap = 7;
        const leftBoundary = Math.max(edge, dialogRect?.left ?? edge);
        const rightBoundary = Math.min(
            window.innerWidth - edge,
            dialogRect?.right ?? window.innerWidth - edge
        );
        const topBoundary = Math.max(edge, dialogRect?.top ?? edge);
        const bottomBoundary = Math.min(
            window.innerHeight - edge,
            dialogRect?.bottom ?? window.innerHeight - edge
        );
        const availableWidth = Math.max(0, rightBoundary - leftBoundary);
        const kind = combobox.dataset.sharedCatalogKind || "";
        const preferredWidth = kind === "product"
            ? Math.max(triggerRect.width, 520)
            : Math.max(triggerRect.width, 320);
        const width = Math.min(preferredWidth, availableWidth);
        const left = Math.max(
            leftBoundary,
            Math.min(triggerRect.left, rightBoundary - width)
        );
        const roomBelow = bottomBoundary - triggerRect.bottom - gap;
        const roomAbove = triggerRect.top - topBoundary - gap;
        const openAbove = roomBelow < 220 && roomAbove > roomBelow;
        const availableHeight = Math.max(
            150,
            Math.min(410, openAbove ? roomAbove : roomBelow)
        );

        dropdown.classList.add("is-viewport-positioned");
        dropdown.style.left = left + "px";
        dropdown.style.width = width + "px";
        dropdown.style.right = "auto";
        dropdown.style.top = openAbove ? "auto" : triggerRect.bottom + gap + "px";
        dropdown.style.bottom = openAbove
            ? window.innerHeight - triggerRect.top + gap + "px"
            : "auto";

        if (options) {
            const dropdownChrome = Math.max(
                62,
                dropdown.offsetHeight - options.offsetHeight
            );
            options.style.maxHeight = Math.max(
                88,
                Math.min(340, availableHeight - dropdownChrome)
            ) + "px";
        }

        dialog?.classList.add("catalog-combobox-open");
    }

    function resetComboboxDropdown(combobox) {
        const dropdown = combobox?.querySelector(
            ".brand-combobox-dropdown"
        );
        const dialog = combobox?.closest(".modal-dialog, .erp-modal-dialog");

        if (dropdown) {
            dropdown.classList.remove("is-viewport-positioned");
            dropdown.removeAttribute("style");
            dropdown.querySelector(".brand-combobox-options")
                ?.style.removeProperty("max-height");
        }
        if (dialog && !dialog.querySelector("[data-brand-combobox].open")) {
            dialog.classList.remove("catalog-combobox-open");
        }
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
                        resetComboboxDropdown(openCombobox);
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

        if (isOpen) {
            window.requestAnimationFrame(function() {
                positionComboboxDropdown(combobox);
            });
        } else {
            resetComboboxDropdown(combobox);
        }

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

    window.addEventListener("resize", function() {
        document.querySelectorAll("[data-brand-combobox].open")
            .forEach(positionComboboxDropdown);
    });
    document.addEventListener("scroll", function() {
        document.querySelectorAll("[data-brand-combobox].open")
            .forEach(positionComboboxDropdown);
    }, true);

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
            .replace(/\s+/g, " ")
            .trim()
            .toLocaleLowerCase("ru")
            .replaceAll("ё", "е");
    };

    window.normalizeCatalogProductImageUrls = function(product) {
        if (!product || typeof product !== "object") return [];

        const urls = [];
        const seenUrls = new Set();
        const seenIds = new Set();
        const baseUrl = document.baseURI || window.location?.href;
        const allowedProtocols = new Set([
            "http:", "https:", "blob:", "data:",
        ]);

        function append(rawImage) {
            if (Array.isArray(rawImage)) {
                rawImage.forEach(append);
                return;
            }

            const image = rawImage && typeof rawImage === "object"
                ? rawImage
                : null;
            const rawUrl = image
                ? (
                    image.original_url
                    || image.download_url
                    || image.url
                    || image.src
                    || image.thumbnail_url
                    || image.image_url
                )
                : rawImage;
            const url = typeof rawUrl === "string" ? rawUrl.trim() : "";
            if (!url) return;
            if ([
                "null", "undefined", "none", "false", "[object object]",
            ].includes(url.toLowerCase())) return;

            let normalizedUrl;
            try {
                const parsed = new URL(url, baseUrl);
                if (
                    !allowedProtocols.has(parsed.protocol)
                    || (
                        parsed.protocol === "data:"
                        && !url.toLowerCase().startsWith("data:image/")
                    )
                ) {
                    return;
                }
                parsed.hash = "";
                normalizedUrl = parsed.href;
            } catch (_error) {
                return;
            }

            const rawId = image
                ? (
                    image.id
                    ?? image.file_id
                    ?? image.external_file_id
                    ?? image.ID
                )
                : "";
            const imageId = String(rawId ?? "").trim();
            if (
                seenUrls.has(normalizedUrl)
                || (imageId && seenIds.has(imageId))
            ) {
                return;
            }

            seenUrls.add(normalizedUrl);
            if (imageId) seenIds.add(imageId);
            urls.push(url);
        }

        [
            product.image_url,
            product.thumbnail_url,
            product.detail_image_url,
            product.preview_image_url,
            product.detail_image,
            product.preview_image,
            product.DETAIL_PICTURE,
            product.PREVIEW_PICTURE,
            product.gallery,
            product.images,
            product.GALLERY,
            product.MORE_PHOTO,
        ].forEach(append);

        return urls;
    };

    function catalogOptionStartsWith(option, query) {
        if (
            option.closest("[data-shared-catalog-kind]")
                ?.dataset.sharedCatalogKind === "product"
            && option.dataset.catalogItem
        ) {
            try {
                const item = JSON.parse(option.dataset.catalogItem);
                return [item.name, item.article, item.barcode]
                    .some(function(value) {
                        return window.normalizeComboboxSearchValue(value)
                            .startsWith(query);
                    });
            } catch (error) {
                return false;
            }
        }

        const label = option.querySelector(
            ".brand-combobox-option-label"
        );
        const searchValue =
            option.dataset.brandSearch
            || (label ? label.textContent : "");
        return window.normalizeComboboxSearchValue(searchValue)
            .startsWith(query);
    }

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
                        ? catalogOptionStartsWith(option, query)
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
            if (
                visibleCount === 0
                && combobox.classList.contains("is-loading")
            ) {
                emptyMessage.textContent =
                    "Ищем по всему каталогу…";
            }
            emptyMessage.hidden = visibleCount !== 0;
        }

        if (typeof window.updateCatalogCreateAction === "function") {
            window.updateCatalogCreateAction(combobox, value);
        }

        combobox
            .querySelectorAll(".catalog-combobox-group-label")
            .forEach(function(label) {
                const group = label.dataset.catalogOptionGroup;
                label.hidden = !Array.from(combobox.querySelectorAll(
                    ".brand-combobox-option:not([hidden])"
                )).some(function(option) {
                    return option.dataset.catalogOptionGroup === group;
                });
            });
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

        combobox
            .querySelectorAll(".brand-combobox-option")
            .forEach(function(option) {
                option.classList.toggle(
                    "active",
                    (option.dataset.brand || "") === value
                );
            });

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
            .querySelectorAll(
                ".brand-combobox-option, .catalog-combobox-group-label"
            )
            .forEach(function(option) {
                option.remove();
            });

        let currentGroup = null;
        (Array.isArray(options) ? options : []).forEach(
            function(option) {
                if (option.group && option.group !== currentGroup) {
                    const groupLabel = document.createElement("div");
                    groupLabel.className = "catalog-combobox-group-label";
                    groupLabel.dataset.catalogOptionGroup = option.group;
                    groupLabel.textContent = option.group;
                    optionsElement.insertBefore(groupLabel, emptyMessage);
                    currentGroup = option.group;
                }
                const button = document.createElement("button");
                button.className = "brand-combobox-option";
                button.type = "button";
                button.setAttribute("role", "option");
                button.dataset.brand = option.value || "";
                button.dataset.brandSearch =
                    option.searchText || option.label || "";
                if (option.group) {
                    button.dataset.catalogOptionGroup = option.group;
                }
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
                label.title = option.title || label.textContent;

                if (option.meta) {
                    const copy = document.createElement("span");
                    const details = document.createElement("span");
                    const meta = document.createElement("span");
                    copy.className = "catalog-combobox-option-copy";
                    details.className = "catalog-combobox-option-details";
                    meta.className = "catalog-combobox-option-meta";
                    meta.textContent = option.meta;
                    details.append(meta, count);
                    copy.append(label, details);
                    if (option.image) {
                        const image = document.createElement("img");
                        image.className = "catalog-combobox-option-image";
                        image.src = option.image;
                        image.alt = "";
                        image.loading = "lazy";
                        image.addEventListener("error", function() {
                            image.remove();
                        }, {once: true});
                        button.append(image);
                    }
                    button.append(copy);
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
                                typeof window.queueSharedCatalogSearch
                                    === "function"
                                && combobox.dataset.sharedCatalogKind
                            ) {
                                window.queueSharedCatalogSearch(
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
                                + ":not([hidden]),"
                                + ".catalog-combobox-action"
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
                                if (activeOption.matches(
                                    "[data-catalog-create-action]"
                                )) {
                                    activeOption.click();
                                } else {
                                    window.selectBrandOption(
                                        activeOption
                                    );
                                }
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
    const catalogSearchWindows = new WeakMap();

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

    function sharedCatalogIdValue(value) {
        return value === null || value === undefined
            ? ""
            : String(value);
    }

    function selectedSharedCatalogId(combobox) {
        return sharedCatalogIdValue(
            sharedCatalogIdInput(combobox)?.value
        );
    }

    function catalogSearchContext(combobox) {
        const scope = sharedCatalogScope(combobox);
        const kind = combobox?.dataset.sharedCatalogKind || "";
        return [
            kind,
            kind === "brand" ? "" : selectedSharedCatalogId(
                sharedCatalogCombobox(scope, "brand")
            ),
            kind === "product" ? selectedSharedCatalogId(
                sharedCatalogCombobox(scope, "category")
            ) : "",
        ].join("|");
    }

    function catalogSearchWindow(combobox) {
        const context = catalogSearchContext(combobox);
        let state = catalogSearchWindows.get(combobox);
        if (!state || state.context !== context) {
            state = {context, queries: new Map()};
            catalogSearchWindows.set(combobox, state);
        }
        return state.queries;
    }

    function cacheCatalogSearchOptions(combobox, query, options) {
        const queries = catalogSearchWindow(combobox);
        const key = window.normalizeComboboxSearchValue(query);
        queries.set(key, options);
        if (queries.size > 12) {
            const oldestKey = queries.keys().next().value;
            if (oldestKey) queries.delete(oldestKey);
        }
    }

    function restoreCatalogSearchOptions(combobox, query) {
        const normalizedQuery = window.normalizeComboboxSearchValue(query);
        const queries = catalogSearchWindow(combobox);
        let cachedKey = "";
        queries.forEach(function(options, candidate) {
            if (
                normalizedQuery.startsWith(candidate)
                && candidate.length > cachedKey.length
            ) {
                cachedKey = candidate;
            }
        });
        const options = queries.get(cachedKey);
        if (!options) return false;
        window.replaceCatalogComboboxOptions(
            combobox,
            options,
            "Ничего не найдено"
        );
        return true;
    }

    function sharedCatalogProductLabel(item) {
        return String(item.name || "Товар без названия");
    }

    function sharedCatalogStockValue(item) {
        const value = item.stock_display ?? item.stock ?? 0;
        const normalized = String(value).trim().replace(/\s+/g, " ");
        const numeric = Number(normalized.replaceAll(" ", ""));

        if (!Number.isFinite(numeric)) {
            return normalized || "0";
        }

        return new Intl.NumberFormat("ru-RU", {
            maximumFractionDigits: 20,
        }).format(numeric).replaceAll(" ", " ");
    }

    function sharedCatalogStockDisplay(item) {
        return sharedCatalogStockValue(item) + " ед.";
    }

    function sharedCatalogOrderCountDisplay(item) {
        if (item.orders_count === undefined || item.orders_count === null) {
            return "";
        }

        const count = Math.max(0, Number(item.orders_count) || 0);
        const lastTwo = count % 100;
        const last = count % 10;
        const noun = lastTwo >= 11 && lastTwo <= 14
            ? "заказов"
            : last === 1
                ? "заказ"
                : last >= 2 && last <= 4
                    ? "заказа"
                    : "заказов";
        return count + " " + noun;
    }

    window.updateCatalogCreateAction = function(
        combobox,
        rawQuery
    ) {
        const action = combobox?.querySelector(
            "[data-catalog-create-action]"
        );

        if (!action) {
            return;
        }

        const query = String(rawQuery || "")
            .replace(/\s+/g, " ")
            .trim();
        const normalizedQuery = window.normalizeComboboxSearchValue(query);
        const kind = action.dataset.catalogCreateAction;
        const exactMatch = Boolean(normalizedQuery) && Array.from(
            combobox.querySelectorAll(".brand-combobox-option")
        ).some(function(option) {
            return window.normalizeComboboxSearchValue(
                option.querySelector(
                    ".brand-combobox-option-label"
                )?.textContent || option.dataset.brand || ""
            ) === normalizedQuery;
        });
        const hasMatches = Boolean(combobox.querySelector(
            ".brand-combobox-option:not([hidden])"
        ));
        const trigger = combobox.querySelector(
            ".brand-combobox-trigger"
        );
        const taxonomyAction = kind === "brand" || kind === "category";
        const available = (
            taxonomyAction
                ? !exactMatch
                : Boolean(query) && !hasMatches
        )
            && !combobox.classList.contains("is-loading")
            && !trigger?.disabled;

        action.hidden = !available;
        action.disabled = !available;
        action.dataset.catalogCreateName = query;
        if (taxonomyAction) {
            const noun = kind === "brand" ? "бренд" : "категорию";
            action.textContent = query
                ? '➕ Создать ' + noun + ' «' + query + '»'
                : '➕ Создать ' + (kind === "brand"
                    ? "новый бренд"
                    : "новую категорию");
        } else {
            action.textContent = available
                ? '➕ Создать "' + query + '"'
                : action.dataset.catalogCreateLabel || "➕ Создать";
        }
    };

    function sharedCatalogOption(item, kind, hideCategoryCount) {
        const product = kind === "product";
        return {
            value: product
                ? sharedCatalogIdValue(item.id)
                : String(item.name || ""),
            label: product
                ? sharedCatalogProductLabel(item)
                : String(item.name || ""),
            title: product
                ? sharedCatalogProductLabel(item)
                : String(item.name || ""),
            searchText: [
                item.name,
                item.article,
                item.barcode,
            ].filter(Boolean).join(" "),
            meta: product
                ? "Артикул: " + (item.article || "—")
                : "",
            count: product
                ? [
                    "Остаток: " + sharedCatalogStockValue(item)
                        + (Number(item.stock || 0) <= 0
                            ? " · Нет в наличии"
                            : ""),
                    sharedCatalogOrderCountDisplay(item),
                ].filter(Boolean).join(" · ")
                : kind === "category" && hideCategoryCount
                    ? ""
                    : sharedCatalogStockDisplay(item),
            image: product
                ? String(item.image_url || item.thumbnail_url || "")
                : "",
            item,
            group: "",
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
        combobox.dataset.sharedCatalogSelectedItem = "";
        combobox.dataset.sharedCatalogNewValue = "";
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
        const itemId = sharedCatalogIdValue(item.id);
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
        combobox.dataset.sharedCatalogSelectedItem = JSON.stringify(item);
        combobox.dataset.sharedCatalogNewValue = "";
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
        } else if (
            emptyMessage
            && !combobox.querySelector(
                ".brand-combobox-option:not([hidden])"
            )
            && ["Загрузка…", "Ищем по всему каталогу…"]
                .includes(emptyMessage.textContent)
        ) {
            emptyMessage.textContent = "Ничего не найдено";
        }

        window.updateCatalogCreateAction(
            combobox,
            combobox?.querySelector(
                ".brand-combobox-search"
            )?.value || ""
        );
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
        const globalCategoryOptions = Boolean(
            kind === "category"
            && scope?.dataset.globalCategoryOptions === "true"
        );

        const categoryWithoutBrand = Boolean(
            scope?.dataset.catalogCategoryWithoutBrand === "true"
        );

        if (
            (kind === "category" && brandId === "" && !categoryWithoutBrand)
            || (
                kind === "product"
                && (brandId === "" || categoryId === "")
            )
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
            limit: "200",
        });
        const query = String(rawQuery || "").trim();

        if (query) {
            parameters.set("q", query);
        }
        if (kind !== "brand" && brandId !== "") {
            parameters.set("brand_id", brandId);
        }
        if (kind === "category") {
            parameters.set(
                "category_scope",
                brandId === "" || globalCategoryOptions
                    ? "all"
                    : "brand"
            );
        }
        if (scope?.dataset.catalogInStock === "true") {
            parameters.set("available_for_sale", "1");
        }
        if (kind === "product") {
            parameters.set("category_id", categoryId);
            if (scope?.dataset.catalogInStock === "true") {
                parameters.set("in_stock", "1");
            }
            if (scope?.dataset.catalogOrderCounts === "true") {
                parameters.set("include_order_counts", "1");
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

            if (requestControllers.get(combobox) !== controller) {
                return [];
            }

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
            const requiresAvailableStock = Boolean(
                scope?.dataset.catalogInStock === "true"
            );
            let availableItems = requiresAvailableStock
                ? items.filter((item) => {
                    if (kind === "product") {
                        return item?.active !== false
                            && Number(item?.stock) > 0;
                    }
                    if (kind === "category") {
                        return Number(item?.count) > 0;
                    }
                    return true;
                })
                : items;
            const selectedId = selectedSharedCatalogId(combobox);
            const selectedItem = (() => {
                try {
                    return JSON.parse(
                        combobox.dataset.sharedCatalogSelectedItem
                        || "null"
                    );
                } catch (error) {
                    return null;
                }
            })();

            if (
                selectedId
                && selectedItem
                && (!requiresAvailableStock
                    || kind !== "product"
                    || (
                        selectedItem.active !== false
                        && Number(selectedItem.stock) > 0
                    ))
                && !availableItems.some(
                    (item) => sharedCatalogIdValue(item.id) === selectedId
                )
            ) {
                availableItems = [selectedItem, ...items];
            }

            const groupCategories = Boolean(
                kind === "category"
                && globalCategoryOptions
                && brandId !== ""
                && availableItems.some((item) => item.used_by_brand)
            );
            const options = availableItems.map((item) => {
                const option = sharedCatalogOption(
                    item,
                    kind,
                    globalCategoryOptions
                );
                if (groupCategories) {
                    option.group = item.used_by_brand
                        ? "Категории этого бренда"
                        : "Другие категории";
                }
                return option;
            });
            cacheCatalogSearchOptions(combobox, query, options);
            window.replaceCatalogComboboxOptions(
                combobox,
                options,
                requiresAvailableStock
                    ? "Нет товаров в наличии"
                    : "Ничего не найдено"
            );
            window.filterBrandList(query, combobox);
            return availableItems;
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
        setSharedCatalogLoading(combobox, true);
        restoreCatalogSearchOptions(combobox, query);
        window.filterBrandList(query, combobox);
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

    function categoryNeedsBrand(scope) {
        return scope?.dataset.catalogCategoryWithoutBrand !== "true";
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
                    categoryNeedsBrand(scope)
                    && !selectedSharedCatalogId(
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

                let id = sharedCatalogIdValue(
                    values?.[kind + "Id"]
                );
                const label = String(
                    values?.[kind + "Label"] || ""
                );
                if (
                    kind === "category"
                    && id === ""
                    && label.trim() === "Без категории"
                ) {
                    id = "0";
                }
                const primary = kind === "product" ? id : label;
                const idInput = sharedCatalogIdInput(combobox);

                if (idInput) {
                    idInput.value = id;
                }

                combobox.dataset.sharedCatalogSelectedId = id;
                combobox.dataset.sharedCatalogSelectedLabel = label;
                combobox.dataset.sharedCatalogSelectedItem = "";
                combobox.dataset.sharedCatalogNewValue = "";
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
            categoryNeedsBrand(scope)
            && selectedSharedCatalogId(
                sharedCatalogCombobox(scope, "brand")
            ) === ""
        );
        setCascadeFieldDisabled(
            scope,
            "product",
            selectedSharedCatalogId(
                sharedCatalogCombobox(scope, "brand")
            ) === ""
            || selectedSharedCatalogId(
                sharedCatalogCombobox(scope, "category")
            ) === ""
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
            categoryNeedsBrand(scope) && !selectedSharedCatalogId(brand)
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

    function initializeSharedCatalogInlineCreation() {
        if (document.documentElement.dataset.catalogInlineCreateBound === "1") {
            return;
        }

        document.documentElement.dataset.catalogInlineCreateBound = "1";
        document.addEventListener("click", async function(event) {
            const action = event.target.closest(
                "[data-catalog-create-action]"
            );

            if (!action || action.disabled) {
                return;
            }

            const combobox = action.closest(
                "[data-shared-catalog-kind]"
            );
            const scope = sharedCatalogScope(combobox);
            const kind = action.dataset.catalogCreateAction;
            const name = String(
                action.dataset.catalogCreateName || ""
            ).replace(/\s+/g, " ").trim();

            if (!combobox || !scope) {
                return;
            }

            event.preventDefault();
            if (!name) {
                const search = combobox.querySelector(
                    ".brand-combobox-search"
                );
                search?.focus();
                return;
            }
            const brandId = selectedSharedCatalogId(
                sharedCatalogCombobox(scope, "brand")
            );
            const categoryId = selectedSharedCatalogId(
                sharedCatalogCombobox(scope, "category")
            );
            const paths = {
                brand: "/api/v1/brands",
                category: "/api/v1/categories",
                product: "/api/v1/products",
            };
            const payload = kind === "brand"
                ? {name}
                : kind === "category"
                    ? {name, brand_id: Number(brandId)}
                    : {
                        name,
                        article: "",
                        brand_id: Number(brandId),
                        category_id: Number(categoryId),
                        brand: "",
                        category: "",
                        cell: "",
                        stock: 0,
                        product_image: null,
                    };
            const empty = combobox.querySelector(
                "[data-brand-empty]"
            );

            action.disabled = true;
            action.textContent = "Создаём…";
            setSharedCatalogLoading(combobox, true);

            try {
                const response = await fetch(paths[kind], {
                    method: "POST",
                    credentials: "same-origin",
                    headers: {
                        Accept: "application/json",
                        "Content-Type": "application/json",
                        "X-CSRF-Token": catalogCsrfToken(scope),
                    },
                    body: JSON.stringify(payload),
                });
                const result = await response.json();

                if (
                    response.status === 409
                    && result?.fields?.existing
                    && (kind === "brand" || kind === "category")
                ) {
                    combobox.dataset.catalogInlineCreating = "true";
                    try {
                        window.setSharedCatalogComboboxValue(
                            combobox,
                            result.fields.existing
                        );
                    } finally {
                        delete combobox.dataset.catalogInlineCreating;
                    }
                    window.setBrandDropdownOpen(combobox, false);
                    return;
                }

                if (!response.ok) {
                    throw new Error(
                        result?.message
                        || result?.error?.message
                        || "Не удалось сохранить значение"
                    );
                }

                if (kind === "brand" || kind === "category") {
                    combobox.dataset.catalogInlineCreating = "true";
                }
                try {
                    window.setSharedCatalogComboboxValue(
                        combobox,
                        result.data
                    );
                } finally {
                    delete combobox.dataset.catalogInlineCreating;
                }
                combobox.dataset.sharedCatalogNewValue =
                    result?.meta?.created === false ? "false" : "true";
                combobox.dispatchEvent(
                    new CustomEvent("shared-catalog:created", {
                        bubbles: true,
                        detail: {
                            kind,
                            item: result.data,
                            created: result?.meta?.created !== false,
                            reactivated: result?.meta?.reactivated === true,
                        },
                    })
                );
                const search = combobox.querySelector(
                    ".brand-combobox-search"
                );

                if (search) {
                    search.value = "";
                }
                await window.loadSharedCatalogOptions(combobox, "");
                window.setBrandDropdownOpen(combobox, false);
            } catch (error) {
                if (empty) {
                    empty.textContent = error.message
                        || "Не удалось сохранить значение";
                    empty.hidden = false;
                }
            } finally {
                setSharedCatalogLoading(combobox, false);
                window.updateCatalogCreateAction(
                    combobox,
                    combobox.querySelector(
                        ".brand-combobox-search"
                    )?.value || ""
                );
            }
        });
    }

    window.initializeSharedCatalogCascades = function(root) {
        (root || document)
            .querySelectorAll("[data-shared-catalog-scope]")
            .forEach(bindSharedCatalogScope);
        initializeSharedCatalogInlineCreation();
    };

    document.addEventListener("DOMContentLoaded", function() {
        window.initializeSharedCatalogCascades(document);
    });
})();
