(() => {
  "use strict";
  const dialog = document.getElementById("manual-product-dialog");
  const form = document.getElementById("manual-product-form");
  const error = document.getElementById("manual-product-error");
  let onCreated = null;
  let pending = false;
  window.ERPManualProduct = {
    open(callback) {
      onCreated = callback;
      form.reset();
      error.hidden = true;
      dialog.showModal();
    },
  };
  document.getElementById("cancel-manual-product").onclick = () =>
    dialog.close();
  dialog.addEventListener("cancel", (event) => {
    if (pending) event.preventDefault();
  });
  form.onsubmit = async (event) => {
    event.preventDefault();
    if (pending) return;
    const body = new FormData(form);
    pending = true;
    for (const input of form.elements) input.disabled = true;
    error.hidden = true;
    try {
      const response = await fetch("/api/v1/products", {
        method: "POST",
        headers: {
          "X-CSRF-Token": body.get("csrf_token") || "",
        },
        body,
      });
      const payload = await response.json();
      if (!response.ok || payload.ok === false)
        throw new Error(payload.message || "Не удалось создать товар.");
      dialog.close();
      if (onCreated) onCreated(payload.data);
      else location.reload();
    } catch (exception) {
      error.textContent = exception.message;
      error.hidden = false;
    } finally {
      pending = false;
      for (const input of form.elements) input.disabled = false;
    }
  };
})();
