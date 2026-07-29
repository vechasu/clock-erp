(function initializeErpPeriodPickerComponent() {
    function parseIsoDate(value) {
        const parts = String(value || "")
            .split("-")
            .map(Number);

        if (
            parts.length !== 3
            || parts.some(Number.isNaN)
        ) {
            return null;
        }

        const date = new Date(
            parts[0],
            parts[1] - 1,
            parts[2]
        );

        return date.getFullYear() === parts[0]
            && date.getMonth() === parts[1] - 1
            && date.getDate() === parts[2]
            ? date
            : null;
    }

    function isoDate(date) {
        return [
            date.getFullYear(),
            String(date.getMonth() + 1).padStart(2, "0"),
            String(date.getDate()).padStart(2, "0"),
        ].join("-");
    }

    function displayDate(value) {
        const date = parseIsoDate(value);

        return date
            ? String(date.getDate()).padStart(2, "0")
                + "."
                + String(date.getMonth() + 1).padStart(2, "0")
                + "."
                + date.getFullYear()
            : "";
    }

    function createLocalPopover(root, trigger, popup, onClose) {
        function close(options) {
            if (popup.hidden) {
                return;
            }

            popup.hidden = true;
            trigger.setAttribute("aria-expanded", "false");
            onClose();

            if (options && options.returnFocus) {
                trigger.focus();
            }
        }

        document.addEventListener("click", function(event) {
            if (
                !popup.hidden
                && !root.contains(event.target)
            ) {
                close();
            }
        });
        document.addEventListener("keydown", function(event) {
            if (event.key === "Escape" && !popup.hidden) {
                close({returnFocus: true});
            }
        });

        return {
            open: function() {
                popup.hidden = false;
                trigger.setAttribute("aria-expanded", "true");
            },
            close: close,
            isOpen: function() {
                return !popup.hidden;
            },
        };
    }

    window.initializeErpPeriodPicker = function(root, options) {
        if (!root || root.dataset.periodPickerReady === "1") {
            return null;
        }

        const settings = options || {};
        const fromInput = settings.fromInput
            || root.querySelector("[data-period-from]");
        const toInput = settings.toInput
            || root.querySelector("[data-period-to]");
        const trigger = root.querySelector(
            ".warehouse-date-trigger"
        );
        const popup = root.querySelector(
            ".warehouse-calendar-popup"
        );
        const monthLabel = root.querySelector(
            "[data-calendar-month]"
        );
        const daysGrid = root.querySelector(
            "[data-calendar-days]"
        );
        const rangeLabel = root.querySelector(
            "[data-date-range-label]"
        );
        const externalClearButton = settings.clearButton || null;

        if (
            !fromInput
            || !toInput
            || !trigger
            || !popup
            || !monthLabel
            || !daysGrid
        ) {
            return null;
        }

        const monthNames = [
            "январь",
            "февраль",
            "март",
            "апрель",
            "май",
            "июнь",
            "июль",
            "август",
            "сентябрь",
            "октябрь",
            "ноябрь",
            "декабрь",
        ];
        let draftFrom = fromInput.value;
        let draftTo = toInput.value;
        const initialMonth = parseIsoDate(draftFrom) || new Date();
        let visibleMonth = new Date(
            initialMonth.getFullYear(),
            initialMonth.getMonth(),
            1
        );

        function updateLabel() {
            const hasPeriod = Boolean(
                fromInput.value || toInput.value
            );

            if (rangeLabel) {
                rangeLabel.textContent = fromInput.value
                    ? displayDate(fromInput.value)
                        + " — "
                        + displayDate(
                            toInput.value || fromInput.value
                        )
                    : "Период";
            }

            trigger.classList.toggle("is-active", hasPeriod);
            root.classList.toggle("has-active-period", hasPeriod);

            if (externalClearButton) {
                externalClearButton.hidden = !hasPeriod;
            }
        }

        function notifyChange() {
            updateLabel();

            if (typeof settings.onChange === "function") {
                settings.onChange({
                    dateFrom: fromInput.value,
                    dateTo: toInput.value,
                });
            }
        }

        const onClose = function() {
            root.classList.remove("is-open");
        };
        const popover = settings.popoverManager
            ? settings.popoverManager.register({
                trigger: trigger,
                panel: popup,
                onClose: onClose,
            })
            : createLocalPopover(
                root,
                trigger,
                popup,
                onClose
            );

        function closeCalendar(returnFocus) {
            root.classList.remove("is-open");
            popover.close({returnFocus: Boolean(returnFocus)});
        }

        function renderCalendar() {
            const year = visibleMonth.getFullYear();
            const month = visibleMonth.getMonth();
            monthLabel.textContent =
                monthNames[month] + " " + year;
            daysGrid.replaceChildren();

            const firstDay = new Date(year, month, 1);
            const mondayOffset =
                (firstDay.getDay() + 6) % 7;
            const firstVisible = new Date(
                year,
                month,
                1 - mondayOffset
            );

            for (let index = 0; index < 42; index += 1) {
                const date = new Date(
                    firstVisible.getFullYear(),
                    firstVisible.getMonth(),
                    firstVisible.getDate() + index
                );
                const value = isoDate(date);
                const button = document.createElement("button");
                const number = document.createElement("span");

                button.type = "button";
                button.className = "warehouse-calendar-day";
                number.textContent = String(date.getDate());
                button.appendChild(number);

                if (date.getMonth() !== month) {
                    button.classList.add("is-outside");
                }
                if (value === draftFrom) {
                    button.classList.add("is-start");
                }
                if (value === draftTo) {
                    button.classList.add("is-end");
                }
                if (
                    draftFrom
                    && draftTo
                    && value >= draftFrom
                    && value <= draftTo
                ) {
                    button.classList.add("is-in-range");
                }

                button.addEventListener("click", function() {
                    if (!draftFrom || draftTo) {
                        draftFrom = value;
                        draftTo = "";
                    } else if (value < draftFrom) {
                        draftTo = draftFrom;
                        draftFrom = value;
                    } else {
                        draftTo = value;
                    }

                    renderCalendar();
                });
                daysGrid.appendChild(button);
            }
        }

        trigger.addEventListener("click", function() {
            if (popover.isOpen()) {
                closeCalendar();
                return;
            }

            draftFrom = fromInput.value;
            draftTo = toInput.value;
            const selectedMonth = parseIsoDate(draftFrom);

            if (selectedMonth) {
                visibleMonth = new Date(
                    selectedMonth.getFullYear(),
                    selectedMonth.getMonth(),
                    1
                );
            }

            root.classList.add("is-open");
            renderCalendar();
            popover.open();
        });

        root.querySelector("[data-calendar-prev]")
            .addEventListener("click", function() {
                visibleMonth = new Date(
                    visibleMonth.getFullYear(),
                    visibleMonth.getMonth() - 1,
                    1
                );
                renderCalendar();
            });

        root.querySelector("[data-calendar-next]")
            .addEventListener("click", function() {
                visibleMonth = new Date(
                    visibleMonth.getFullYear(),
                    visibleMonth.getMonth() + 1,
                    1
                );
                renderCalendar();
            });

        root.querySelector("[data-calendar-reset]")
            .addEventListener("click", function() {
                draftFrom = "";
                draftTo = "";
                fromInput.value = "";
                toInput.value = "";
                closeCalendar();
                notifyChange();
            });

        root.querySelector("[data-calendar-apply]")
            .addEventListener("click", function() {
                if (draftFrom && !draftTo) {
                    draftTo = draftFrom;
                }

                fromInput.value = draftFrom;
                toInput.value = draftTo;
                closeCalendar();
                notifyChange();
            });

        if (externalClearButton) {
            externalClearButton.addEventListener(
                "click",
                function() {
                    draftFrom = "";
                    draftTo = "";
                    fromInput.value = "";
                    toInput.value = "";
                    notifyChange();
                }
            );
        }

        root.dataset.periodPickerReady = "1";
        updateLabel();

        return {
            clear: function() {
                draftFrom = "";
                draftTo = "";
                fromInput.value = "";
                toInput.value = "";
                notifyChange();
            },
            updateLabel: updateLabel,
        };
    };
})();
