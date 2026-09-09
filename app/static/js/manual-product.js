(() => {
  "use strict";
  const dialog = document.getElementById("manual-product-dialog");
  const form = document.getElementById("manual-product-form");
  const error = document.getElementById("manual-product-error");
  const nameInput = document.getElementById("manualProductName");
  const productCombobox = document.getElementById("manualProductCombobox");
  const imageInput = document.getElementById("manualProductImage");
  const imagePreview = document.getElementById("manualProductImagePreview");
  const imagePlaceholder = document.getElementById("manualProductImagePlaceholder");
  const imageStatus = document.getElementById("manualProductImageStatus");
  let onCreated = null;
  let pending = false;
  let previewUrl = "";

  const field = (name) => form.elements.namedItem(name);
  const clearCombobox = (combobox) => {
    if (window.clearSharedCatalogCombobox) window.clearSharedCatalogCombobox(combobox);
  };
  function resetPhoto() {
    if (previewUrl) URL.revokeObjectURL(previewUrl);
    previewUrl = "";
    imagePreview.removeAttribute("src");
    imagePreview.hidden = true;
    imagePlaceholder.hidden = false;
    imageStatus.textContent = "Фотография не выбрана";
  }
  function prepareProduct(name) {
    const normalized = String(name || "").replace(/\s+/g, " ").trim();
    nameInput.value = normalized;
    clearCombobox(productCombobox);
    productCombobox.dataset.sharedCatalogSelectedLabel = normalized;
    window.setBrandComboboxValue(productCombobox, "", normalized ? `Новый товар: "${normalized}"` : "Введите название товара");
    window.setBrandDropdownOpen(productCombobox, false);
    error.hidden = true;
  }
  function resetForm() {
    form.reset();
    clearCombobox(document.getElementById("manualProductBrandCombobox"));
    clearCombobox(document.getElementById("manualProductCategoryCombobox"));
    clearCombobox(productCombobox);
    nameInput.value = "";
    resetPhoto();
    error.hidden = true;
  }
  window.ERPManualProduct = {open(callback) {
    onCreated = callback;
    resetForm();
    dialog.showModal();
    requestAnimationFrame(() => document.getElementById("manualProductBrandComboboxTrigger")?.focus());
  }};
  form.addEventListener("click", (event) => {
    const action = event.target.closest('[data-catalog-create-action="product"]');
    if (!action || !form.contains(action)) return;
    event.preventDefault();
    event.stopPropagation();
    event.stopImmediatePropagation();
    if (!field("brand_id").value || !field("category_id").value) {
      error.textContent = "Сначала выберите бренд и категорию.";
      error.hidden = false;
      return;
    }
    prepareProduct(action.dataset.catalogCreateName || "");
  }, true);
  form.addEventListener("catalog-combobox:change", (event) => {
    const kind = event.target?.dataset?.sharedCatalogKind;
    if (kind === "brand" || kind === "category") {
      nameInput.value = "";
      clearCombobox(productCombobox);
    }
  });
  imageInput.addEventListener("change", () => {
    resetPhoto();
    const file = imageInput.files?.[0];
    if (!file) return;
    previewUrl = URL.createObjectURL(file);
    imagePreview.src = previewUrl;
    imagePreview.hidden = false;
    imagePlaceholder.hidden = true;
    imageStatus.textContent = file.name;
  });
  const close = () => { if (!pending) dialog.close(); };
  document.getElementById("cancel-manual-product").onclick = close;
  form.querySelector("[data-manual-product-cancel]").onclick = close;
  dialog.addEventListener("cancel", (event) => { if (pending) event.preventDefault(); });
  form.onsubmit = async (event) => {
    event.preventDefault();
    if (pending) return;
    if (!nameInput.value.trim()) {
      error.textContent = "Введите название нового товара в поле «Товар».";
      error.hidden = false;
      document.getElementById("manualProductComboboxTrigger")?.focus();
      return;
    }
    const body = new FormData(form);
    body.delete("product_id");
    pending = true;
    for (const input of form.elements) input.disabled = true;
    error.hidden = true;
    try {
      const response = await fetch("/api/v1/products", {method: "POST", headers: {"X-CSRF-Token": body.get("csrf_token") || ""}, body});
      const payload = await response.json();
      if (!response.ok || payload.ok === false) throw new Error(payload.message || "Не удалось создать товар.");
      dialog.close();
      if (onCreated) onCreated(payload.data); else location.reload();
    } catch (exception) {
      error.textContent = exception.message;
      error.hidden = false;
    } finally {
      pending = false;
      for (const input of form.elements) input.disabled = false;
    }
  };
})();
