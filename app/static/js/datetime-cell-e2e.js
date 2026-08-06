window.addEventListener("load", () => {
    const root = document.documentElement;

    try {
        const cells = Array.from(
            document.querySelectorAll(".erp-datetime-cell"),
        ).filter((cell) => cell.getClientRects().length > 0);

        if (!cells.length) {
            throw new Error("missing-datetime-cells");
        }

        cells.forEach((cell) => {
            const date = cell.querySelector(".erp-datetime-date");
            const time = cell.querySelector(".erp-datetime-time");

            if (!date || !/^\d{2}\.\d{2}\.\d{4}$/.test(date.textContent.trim())) {
                return;
            }

            if (!time || !/^\d{2}:\d{2}$/.test(time.textContent.trim())) {
                throw new Error("invalid-time-format");
            }

            const dateRect = date.getBoundingClientRect();
            const timeRect = time.getBoundingClientRect();

            if (
                timeRect.top <= dateRect.top
                || Math.abs(timeRect.left - dateRect.left) > 1
                || date.scrollWidth > date.clientWidth + 1
                || time.scrollWidth > time.clientWidth + 1
            ) {
                throw new Error("datetime-layout");
            }
        });

        if (root.scrollWidth > root.clientWidth + 1) {
            throw new Error("page-horizontal-overflow");
        }

        root.dataset.datetimeE2e = "pass";
    } catch (error) {
        root.dataset.datetimeE2e = "fail-" + error.message;
    }
});
