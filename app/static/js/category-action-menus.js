(function (root, factory) {
    const api = factory();
    if (typeof module === "object" && module.exports) {
        module.exports = api;
    }
    root.CategoryActionMenus = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
    function createController(options) {
        const getMenus = options.getMenus;
        let activeCategoryMenu = null;
        let activeCategoryMenuId = null;

        function closeAll() {
            getMenus().forEach(function (menu) {
                menu.open = false;
            });
            activeCategoryMenu = null;
            activeCategoryMenuId = null;
        }

        function toggle(menu) {
            const shouldOpen = menu !== activeCategoryMenu || !menu.open;
            closeAll();
            if (shouldOpen) {
                menu.open = true;
                activeCategoryMenu = menu;
                activeCategoryMenuId = menu.dataset.categoryMenuId;
            }
        }

        function handleClick(event) {
            const summary = event.target.closest(".action-menu > summary");
            if (summary) {
                event.preventDefault();
                toggle(summary.closest(".action-menu"));
                return;
            }
            if (event.target.closest(".menu-popover a, .menu-popover button")) {
                closeAll();
                return;
            }
            if (!event.target.closest(".action-menu")) {
                closeAll();
            }
        }

        function handleKeydown(event) {
            if (event.key === "Escape") {
                closeAll();
            }
        }

        return {
            closeAll: closeAll,
            handleClick: handleClick,
            handleKeydown: handleKeydown,
            toggle: toggle,
            getActiveCategoryMenuId: function () {
                return activeCategoryMenuId;
            },
        };
    }

    function bind(documentObject) {
        const controller = createController({
            getMenus: function () {
                return Array.from(
                    documentObject.querySelectorAll(".action-menu")
                );
            },
        });
        documentObject.addEventListener("click", controller.handleClick);
        documentObject.addEventListener("keydown", controller.handleKeydown);
        return controller;
    }

    return {bind: bind, createController: createController};
});
