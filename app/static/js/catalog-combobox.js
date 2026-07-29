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

        const trigger = getComboboxTrigger(combobox);

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

        const trigger = getComboboxTrigger(combobox);
        const hiddenInput = combobox.querySelector(
            ".brand-combobox-hidden"
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
        const value = button.dataset.brand || "";
        const combobox = button.closest("[data-brand-combobox]");
        const label = button.querySelector(
            ".brand-combobox-option-label"
        );
        const displayValue = label
            ? label.textContent.trim()
            : value;

        window.setBrandComboboxValue(
            combobox,
            value,
            displayValue
        );

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
                });
                searchInput.addEventListener("input", function() {
                    if (
                        combobox.dataset
                            .clearSelectionOnSearchClear === "true"
                    ) {
                        window.setBrandComboboxValue(
                            combobox,
                            ""
                        );
                    }

                    window.filterBrandList(
                        searchInput.value,
                        combobox
                    );
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
                                window.setBrandComboboxValue(
                                    combobox,
                                    ""
                                );
                            }

                            window.filterBrandList("", combobox);
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
