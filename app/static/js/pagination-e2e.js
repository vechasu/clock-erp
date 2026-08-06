document.addEventListener("DOMContentLoaded", () => {
    try {
        const pagination = document.querySelector("[data-erp-pagination]");
        const size = pagination?.querySelector('select[name="per_page"]');
        const options = Array.from(size?.options || []).map((item) => item.value);
        const current = pagination?.querySelector('.erp-pagination-number[aria-current="page"]');
        const previous = pagination?.querySelector('[aria-label="Предыдущая страница"]');
        const next = pagination?.querySelector('[aria-label="Следующая страница"]');
        if (!pagination || options.join(",") !== "25,50,100") throw new Error("options");
        if (!current) throw new Error("current-page");
        if (!previous || !next) throw new Error("steps");
        if (document.documentElement.scrollWidth > document.documentElement.clientWidth + 1) {
            throw new Error("horizontal-overflow");
        }
        if (window.innerWidth <= 600) {
            if (previous.getBoundingClientRect().height < 44) throw new Error("mobile-target");
            const numbers = pagination.querySelector(".erp-pagination-numbers");
            if (getComputedStyle(numbers).display !== "none") throw new Error("mobile-numbers");
        }
        document.documentElement.dataset.paginationE2e = "pass";
    } catch (error) {
        document.documentElement.dataset.paginationE2e = "fail-" + error.message;
    }
});
