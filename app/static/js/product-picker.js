/* Rendering only: callers own data sources, selection state and business actions. */
(() => {
  const node = (tag, className, text) => {
    const element = document.createElement(tag);
    element.className = className;
    if (text != null) element.textContent = text;
    return element;
  };
  function photo(url, large = false) {
    const box = node(
      "span",
      "picker-photo" + (large ? " picker-photo-large" : ""),
      "Нет фото",
    );
    if (url) {
      const img = node("img", "");
      img.alt = "";
      img.src = url;
      img.addEventListener(
        "error",
        () => box.replaceChildren(document.createTextNode("Нет фото")),
        { once: true },
      );
      box.replaceChildren(img);
    }
    return box;
  }
  function metadata(p) {
    const stock = p.is_bundle
      ? p.available_to_assemble
      : p.is_physical_component
        ? p.physical_stock
        : p.stock;
    return [
      ["Артикул", p.article],
      ["Бренд", p.brand],
      ["Категория", p.category],
      [p.is_bundle ? "Доступно к сборке" : "Остаток", stock],
    ].filter(
      ([, value]) => value !== null && value !== undefined && value !== "",
    );
  }
  function results(container, products, select, idKey = "id") {
    container.replaceChildren();
    products.forEach((p) => {
      const row = node("button", "picker-result");
      row.type = "button";
      row.dataset.productId = String(p[idKey]);
      row.setAttribute("aria-pressed", "false");
      const label = node("span", "picker-result-label");
      label.append(node("strong", "", p.name));
      const fields = metadata(p);
      for (let i = 0; i < fields.length; i += 2) {
        label.append(
          node(
            "small",
            "",
            fields
              .slice(i, i + 2)
              .map(([k, v]) => `${k}: ${v}`)
              .join(" · "),
          ),
        );
      }
      row.append(
        photo(p.image_url),
        label,
        node("span", "picker-choose", "Выбрать"),
      );
      row.addEventListener("click", () => select(p));
      container.append(row);
    });
  }
  function highlight(container, id) {
    container
      .querySelectorAll("[data-product-id]")
      .forEach((row) =>
        row.setAttribute(
          "aria-pressed",
          String(row.dataset.productId === String(id)),
        ),
      );
  }
  function preview(container, p) {
    container.replaceChildren();
    if (!p) return;
    const fields = node("div", "picker-details");
    metadata(p).forEach(([key, value]) =>
      fields.append(node("div", "", `${key}: ${value}`)),
    );
    container.append(photo(p.image_url, true), node("h3", "", p.name), fields);
  }
  window.ERPProductPicker = { results, highlight, preview };
})();
