(function (window, document) {
    "use strict";

    const THEMES = Object.freeze([
        "classic",
        "klok-green",
        "bn0024-white",
    ]);
    const DEFAULT_THEME = "classic";
    const STORAGE_KEY = "vechasu-erp-theme-v1";

    function normalizeTheme(value) {
        return THEMES.includes(value) ? value : DEFAULT_THEME;
    }

    function getStorage() {
        try {
            return window.localStorage;
        } catch (error) {
            return null;
        }
    }

    function getStoredTheme() {
        const storage = getStorage();

        if (!storage) {
            return DEFAULT_THEME;
        }

        try {
            const storedTheme = storage.getItem(STORAGE_KEY);
            const theme = normalizeTheme(storedTheme);

            if (storedTheme && storedTheme !== theme) {
                storage.removeItem(STORAGE_KEY);
            }

            return theme;
        } catch (error) {
            return DEFAULT_THEME;
        }
    }

    function setRootTheme(theme) {
        const normalizedTheme = normalizeTheme(theme);

        document.documentElement.dataset.theme = normalizedTheme;
        document.documentElement.dataset.themeReady = "true";
        document.documentElement.style.colorScheme = "light";

        return normalizedTheme;
    }

    function persistTheme(theme) {
        const storage = getStorage();

        if (!storage) {
            return false;
        }

        try {
            storage.setItem(STORAGE_KEY, theme);
            return true;
        } catch (error) {
            return false;
        }
    }

    function syncThemeControls(theme) {
        document.querySelectorAll("[data-theme-option]").forEach(
            function (option) {
                const selected = (
                    option.dataset.themeOption === theme
                );

                option.setAttribute(
                    "aria-checked",
                    selected ? "true" : "false"
                );
                option.tabIndex = selected ? 0 : -1;
                option.classList.toggle("is-selected", selected);

                const state = option.querySelector(
                    "[data-theme-option-state]"
                );

                if (state) {
                    state.textContent = (
                        selected ? "Выбрана" : "Выбрать тему"
                    );
                }
            }
        );
    }

    function applyTheme(value, options) {
        const theme = setRootTheme(value);
        const shouldPersist = (
            !options || options.persist !== false
        );

        if (shouldPersist) {
            persistTheme(theme);
        }

        syncThemeControls(theme);
        window.dispatchEvent(
            new CustomEvent("erp:theme-change", {
                detail: {theme: theme},
            })
        );

        return theme;
    }

    function initializeThemeControls() {
        syncThemeControls(
            normalizeTheme(document.documentElement.dataset.theme)
        );

        document.querySelectorAll("[data-theme-option]").forEach(
            function (option) {
                option.addEventListener("click", function () {
                    if (option.disabled) {
                        return;
                    }
                    applyTheme(option.dataset.themeOption);
                });
                option.addEventListener("keydown", function (event) {
                    if (!["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown", "Home", "End"].includes(event.key)) {
                        return;
                    }
                    var options = Array.from(
                        document.querySelectorAll("[data-theme-option]:not(:disabled)")
                    );
                    var currentIndex = options.indexOf(option);
                    var nextIndex = event.key === "Home"
                        ? 0
                        : event.key === "End"
                            ? options.length - 1
                            : currentIndex + (
                                event.key === "ArrowLeft" || event.key === "ArrowUp" ? -1 : 1
                            );
                    event.preventDefault();
                    var nextOption = options[
                        (nextIndex + options.length) % options.length
                    ];
                    applyTheme(nextOption.dataset.themeOption);
                    nextOption.focus();
                });
            }
        );
    }

    const initialTheme = setRootTheme(getStoredTheme());

    window.ERPTheme = Object.freeze({
        themes: THEMES,
        defaultTheme: DEFAULT_THEME,
        storageKey: STORAGE_KEY,
        normalizeTheme: normalizeTheme,
        getStoredTheme: getStoredTheme,
        applyTheme: applyTheme,
        currentTheme: function () {
            return normalizeTheme(
                document.documentElement.dataset.theme
            );
        },
    });

    if (document.readyState === "loading") {
        document.addEventListener(
            "DOMContentLoaded",
            initializeThemeControls,
            {once: true}
        );
    } else {
        initializeThemeControls();
    }

    syncThemeControls(initialTheme);
}(window, document));
