// @vitest-environment jsdom
import { expect, test } from 'vitest';
import template from '../../app/templates/sales.html?raw';

const quantityFunctions = template
  .split('    function saleAvailableStock(item) {', 2)[1]
  .split('    saleQuantityInput.addEventListener', 1)[0];

test.each([
  [{ stock: 3 }, 2, true],
  [{ stock: 3 }, 4, false],
  [{ stock: 0, is_bundle: true, available_to_assemble: 5 }, 2, true],
  [{ stock: 0, is_bundle: true, available_to_assemble: 1 }, 2, false],
  [{ stock: 99, is_bundle: true, available_to_assemble: 0 }, 1, false],
  [{ stock: 0, is_bundle: true, available_to_assemble: 5 }, 1.5, false],
  [{ stock: 0, is_bundle: true, available_to_assemble: 5 }, 0, false],
  [{ stock: 998, is_physical_component: true, physical_stock: null }, 1, false],
  [{ stock: 998, is_physical_component: true, physical_stock: 3 }, 4, false],
  [{ stock: 0, is_physical_component: true, physical_stock: 3 }, 2, true],
  [null, 1, false],
])('sale form validates physical or assembly availability: %j × %s', (product, quantity, valid) => {
  document.body.innerHTML = '<form><input type="number"><button>Провести</button></form>';
  const result = window.eval(`(() => {
    const manualSaleForm = document.querySelector('form');
    const saleQuantityInput = document.querySelector('input');
    saleQuantityInput.value = ${JSON.stringify(String(quantity))};
    const saleSubmitButton = document.querySelector('button');
    const saleQuantityError = {};
    const saleFormError = {textContent: ''};
    const setSaleFieldError = () => {};
    const setSaleFormError = () => {};
    const sharedSelectedSaleProduct = ${JSON.stringify(product)};
    function saleAvailableStock(item) {${quantityFunctions}
    return validateSaleQuantity();
  })()`);
  expect(result).toBe(valid);
  expect(document.querySelector('button')!.disabled).toBe(!valid);
  expect(document.querySelector('input')!.validationMessage === '').toBe(valid);
});
