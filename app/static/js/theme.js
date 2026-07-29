(function (window, document) {
    "use strict";

    const THEMES = Object.freeze([
        "klok-green",
        "bn0024-white",
    ]);
    const DEFAULT_THEME = "bn0024-white";
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
                    applyTheme(option.dataset.themeOption);
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
