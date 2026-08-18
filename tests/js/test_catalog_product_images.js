const assert = require("node:assert/strict");

global.window = {
    addEventListener() {},
    location: {href: "https://erp.test/app/sales"},
};
global.document = {
    addEventListener() {},
    baseURI: "https://erp.test/app/sales",
};

require("../../app/static/js/catalog-combobox.js");

const normalize = window.normalizeCatalogProductImageUrls;

assert.deepEqual(normalize({image_url: "undefined", gallery: [null, ""]}), []);
assert.deepEqual(
    normalize({image_url: "https://cdn.test/one.jpg", preview_image: ""}),
    ["https://cdn.test/one.jpg"],
);
assert.deepEqual(
    normalize({
        DETAIL_PICTURE: {id: 10, url: "https://cdn.test/one.jpg"},
        gallery: [{id: 11, original_url: "/two.jpg"}],
    }),
    ["https://cdn.test/one.jpg", "/two.jpg"],
);
assert.deepEqual(
    normalize({
        DETAIL_PICTURE: {id: 10, url: "https://cdn.test/one.jpg#detail"},
        PREVIEW_PICTURE: {id: 10, url: "https://cdn.test/one.jpg"},
        gallery: [
            {id: 12, original_url: "https://cdn.test/one.jpg"},
            null,
            undefined,
            "",
            "javascript:alert(1)",
        ],
    }),
    ["https://cdn.test/one.jpg#detail"],
);
