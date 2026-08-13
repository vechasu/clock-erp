const assert = require("node:assert/strict");
const test = require("node:test");

const {createController} = require(
    "../../app/static/js/category-action-menus.js"
);

function setup() {
    const menus = [
        {dataset: {categoryMenuId: "A"}, open: false},
        {dataset: {categoryMenuId: "B"}, open: false},
    ];
    const controller = createController({getMenus: () => menus});
    return {controller, menus};
}

function target(matches) {
    return {
        closest(selector) {
            return matches[selector] || null;
        },
    };
}

test("opens one category menu", () => {
    const {controller, menus} = setup();
    controller.toggle(menus[0]);
    assert.equal(menus[0].open, true);
    assert.equal(controller.getActiveCategoryMenuId(), "A");
});

test("opening B closes A and leaves exactly one menu open", () => {
    const {controller, menus} = setup();
    controller.toggle(menus[0]);
    controller.toggle(menus[1]);
    assert.deepEqual(menus.map((menu) => menu.open), [false, true]);
    assert.equal(menus.filter((menu) => menu.open).length, 1);
});

test("repeated click closes the active menu", () => {
    const {controller, menus} = setup();
    controller.toggle(menus[1]);
    controller.toggle(menus[1]);
    assert.equal(menus.filter((menu) => menu.open).length, 0);
});

test("outside click closes the active menu", () => {
    const {controller, menus} = setup();
    controller.toggle(menus[0]);
    controller.handleClick({target: target({})});
    assert.equal(menus[0].open, false);
});

test("Escape closes the active menu", () => {
    const {controller, menus} = setup();
    controller.toggle(menus[0]);
    controller.handleKeydown({key: "Escape"});
    assert.equal(menus[0].open, false);
});

test("selecting a menu action closes the active menu", () => {
    const {controller, menus} = setup();
    controller.toggle(menus[0]);
    controller.handleClick({
        target: target({".menu-popover a, .menu-popover button": {}}),
    });
    assert.equal(menus[0].open, false);
});
