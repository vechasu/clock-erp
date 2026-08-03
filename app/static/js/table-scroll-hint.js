(function () {
    "use strict";

    function updateScrollHint(container) {
        const maxScrollLeft = Math.max(
            0,
            container.scrollWidth - container.clientWidth
        );
        const hasOverflow = maxScrollLeft > 2;
        const isAtEnd = !hasOverflow
            || container.scrollLeft >= maxScrollLeft - 2;

        container.classList.toggle(
            "has-horizontal-overflow",
            hasOverflow
        );
        container.classList.toggle("is-at-scroll-end", isAtEnd);
    }

    function initializeTableScrollHints(root) {
        root.querySelectorAll("[data-erp-scroll-hint]")
            .forEach(function (container) {
                if (container.dataset.erpScrollHintReady === "1") {
                    updateScrollHint(container);
                    return;
                }

                container.dataset.erpScrollHintReady = "1";
                container.addEventListener(
                    "scroll",
                    function () {
                        updateScrollHint(container);
                    },
                    {passive: true}
                );
                container.addEventListener("keydown", function (event) {
                    if (event.key !== "ArrowLeft"
                        && event.key !== "ArrowRight") {
                        return;
                    }
                    event.preventDefault();
                    container.scrollLeft += event.key === "ArrowRight"
                        ? 80
                        : -80;
                });

                if (typeof ResizeObserver === "function") {
                    const observer = new ResizeObserver(function () {
                        updateScrollHint(container);
                    });
                    observer.observe(container);
                    Array.from(container.children).forEach(function (child) {
                        observer.observe(child);
                    });
                }

                updateScrollHint(container);
            });
    }

    document.addEventListener("DOMContentLoaded", function () {
        initializeTableScrollHints(document);
        if (typeof MutationObserver === "function") {
            const observer = new MutationObserver(function (mutations) {
                if (mutations.some(function (mutation) {
                    return mutation.addedNodes.length > 0;
                })) {
                    initializeTableScrollHints(document);
                }
            });
            observer.observe(document.body, {childList: true, subtree: true});
        }
    });
    window.addEventListener("resize", function () {
        document.querySelectorAll("[data-erp-scroll-hint]")
            .forEach(updateScrollHint);
    });

    window.initializeTableScrollHints = initializeTableScrollHints;
})();
