(function initializeToolbarPopoverCoordinator() {
    "use strict";

    window.createErpToolbarPopoverCoordinator = function (entries) {
        const popovers = (entries || []).filter(function (entry) {
            return entry && entry.trigger && entry.container;
        });

        function closeEntry(entry, returnFocus) {
            const wasOpen = entry.isOpen();

            entry.close();
            if (wasOpen && returnFocus) {
                entry.trigger.focus();
            }
        }

        function closeAll(except, returnFocus) {
            popovers.forEach(function (entry) {
                if (entry !== except) {
                    closeEntry(entry, returnFocus);
                }
            });
        }

        popovers.forEach(function (entry) {
            entry.trigger.addEventListener("click", function () {
                closeAll(entry, false);
            });
        });

        document.addEventListener("click", function (event) {
            popovers.forEach(function (entry) {
                if (!entry.container.contains(event.target)) {
                    closeEntry(entry, false);
                }
            });
        });

        document.addEventListener("keydown", function (event) {
            if (event.key !== "Escape") {
                return;
            }

            const activeEntry = popovers.find(function (entry) {
                return entry.isOpen();
            });

            closeAll(null, false);
            activeEntry?.trigger.focus();
        });

        return {
            closeAll: function () {
                closeAll(null, false);
            },
        };
    };
})();
