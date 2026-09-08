(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const esc = (s) =>
    String(s ?? "").replace(
      /[&<>"']/g,
      (c) =>
        ({
          "&": "&amp;",
          "<": "&lt;",
          ">": "&gt;",
          '"': "&quot;",
          "'": "&#39;",
        })[c],
    );
  const number = (n) => (n == null ? "—" : Number(n).toLocaleString("ru-RU"));
  const image = (url) =>
    /^https?:\/\//.test(url || "") || (url || "").startsWith("/")
      ? `<img src="${esc(url)}" alt="" loading="lazy">`
      : "—";
  const labels = {
    supply: "Поставка",
    sale_cancellation: "Отмена продажи",
    receipt: "Старый приход",
    legacy: "Архивная запись",
    legacy_excel: "Excel — история",
    return: "Возврат продажи",
    manual_adjustment: "Корректировка",
    initial_stock: "Начальный остаток",
    draft: "Черновик",
    posted: "Проведена",
  };
  let tab = new URLSearchParams(location.search).get("tab") || "all";
  let rows = [],
    filtered = [],
    page = 1,
    current = null,
    items = [],
    busy = false;
  const hidden = new Set();
  let sortDirection = "desc";
  const storageKey = () => "erp-supply-columns-" + tab;
  function restoreColumns() {
    hidden.clear();
    try {
      for (const n of JSON.parse(localStorage.getItem(storageKey()) || "[]"))
        hidden.add(n);
    } catch {
      /* Fresh defaults when browser storage is unavailable. */
    }
  }
  restoreColumns();
  async function api(path, options = {}) {
    const headers = {
      "X-CSRF-Token": document.querySelector("meta[name=csrf-token]").content,
      ...options.headers,
    };
    if (options.body && !(options.body instanceof FormData))
      headers["Content-Type"] = "application/json";
    const response = await fetch("/api/v1/receipts/" + path, {
      ...options,
      headers,
    });
    let data;
    try {
      data = await response.json();
    } catch {
      throw new Error("Не удалось получить ответ сервера. Обновите страницу.");
    }
    if (!response.ok || data.ok === false) {
      const error = new Error(data.message || "Операция отклонена сервером.");
      error.status = response.status;
      throw error;
    }
    return data.data;
  }
  function message(text, dialog = false) {
    const el = $(dialog ? "dialog-message" : "message");
    el.textContent = text;
    el.hidden = !text;
  }
  async function action(fn, dialog = false) {
    if (busy) return;
    busy = true;
    document.querySelectorAll("button,input,select,textarea").forEach((b) => {
      b.dataset.wasDisabled = b.disabled;
      b.disabled = true;
    });
    try {
      await fn();
    } catch (e) {
      message(e.message, dialog);
    } finally {
      busy = false;
      document.querySelectorAll("button,input,select,textarea").forEach((b) => {
        b.disabled = b.dataset.wasDisabled === "true";
      });
      renderItems();
      render();
    }
  }
  function options(id, values) {
    const el = $(id),
      previous = el.value,
      label = el.options[0].textContent;
    el.innerHTML =
      `<option value="">${esc(label)}</option>` +
      [...new Set(values.filter(Boolean))]
        .sort()
        .map((v) => `<option>${esc(v)}</option>`)
        .join("");
    el.value = previous;
  }
  async function load() {
    rows = await api(tab === "supplies" ? "supplies" : "movements");
    if (tab === "cancellations")
      rows = rows.filter((r) => r.source_type === "sale_cancellation");
    options(
      "brand",
      rows.flatMap((r) => (r.items ? r.items.map((i) => i.brand) : [r.brand])),
    );
    options(
      "category",
      rows.flatMap((r) =>
        r.items ? r.items.map((i) => i.category) : [r.category],
      ),
    );
    document
      .querySelectorAll("[data-tab]")
      .forEach((b) =>
        b.setAttribute(
          "aria-current",
          b.dataset.tab === tab ? "page" : "false",
        ),
      );
    page = 1;
    render();
  }
  function render() {
    const q = $("query").value.toLocaleLowerCase(),
      from = $("date-from").value,
      to = $("date-to").value;
    filtered = rows.filter((r) => {
      const searchable = [
        r.title,
        r.number,
        r.comment,
        r.name,
        r.article,
        r.brand,
        r.user_name,
        r.created_by,
        r.source_id,
      ]
        .join(" ")
        .toLocaleLowerCase();
      const timestamp = new Date(r.created_at);
      const date = Number.isNaN(timestamp.valueOf())
        ? (r.created_at || "").slice(0, 10)
        : [
            timestamp.getFullYear(),
            String(timestamp.getMonth() + 1).padStart(2, "0"),
            String(timestamp.getDate()).padStart(2, "0"),
          ].join("-");
      return (
        (!q || searchable.includes(q)) &&
        (!from || date >= from) &&
        (!to || date <= to) &&
        (!$("type").value ||
          (r.source_type || r.status) === $("type").value ||
          r.status === $("type").value) &&
        (!$("brand").value ||
          r.brand === $("brand").value ||
          r.items?.some((i) => i.brand === $("brand").value)) &&
        (!$("category").value ||
          r.category === $("category").value ||
          r.items?.some((i) => i.category === $("category").value))
      );
    });
    filtered.sort((a, b) =>
      sortDirection === "desc"
        ? b.created_at.localeCompare(a.created_at)
        : a.created_at.localeCompare(b.created_at),
    );
    const supplies = tab === "supplies";
    $("count-label").textContent = supplies
      ? "Поставок"
      : tab === "cancellations"
        ? "Отмен"
        : "Записей прихода";
    $("count").textContent = number(
      tab === "cancellations"
        ? new Set(filtered.map((r) => r.source_id)).size
        : filtered.length,
    );
    $("quantity-label").textContent =
      tab === "cancellations" ? "Возвращено единиц" : "Принято единиц";
    $("quantity").textContent = number(
      filtered.reduce(
        (sum, r) =>
          sum +
          (supplies
            ? r.status === "posted"
              ? r.total_quantity
              : 0
            : Number(r.quantity)),
        0,
      ),
    );
    const columns = supplies
      ? [
          "Дата",
          "Номер",
          "Название поставки",
          "Комментарий",
          "Позиций",
          "Единиц",
          "Статус",
          "Автор",
          "Действия",
        ]
      : [
          "Дата",
          "Время",
          "Тип прихода",
          "Документ",
          "Комментарий",
          "Бренд",
          "Категория",
          "Фото",
          "Товар",
          "Артикул",
          "Было",
          "Изменение",
          "Стало",
          "Автор",
          "Источник",
        ];
    $("columns").innerHTML = columns
      .map(
        (c, i) =>
          `<label><input type="checkbox" data-col="${i}" ${hidden.has(i) ? "" : "checked"}>${c}</label>`,
      )
      .join("");
    const cell = (v, i, tag = "td") =>
      `<${tag}${tag === "th" ? ' scope="col"' + (i === 0 ? ' aria-sort="' + (sortDirection === "desc" ? "descending" : "ascending") + '"' : "") : ""}${hidden.has(i) ? " hidden" : ""}>${v}</${tag}>`;
    $("records").querySelector("thead").innerHTML =
      "<tr>" +
      columns
        .map((v, i) =>
          cell(
            i === 0
              ? '<button class="button" data-sort-date>Дата ' +
                  (sortDirection === "desc" ? "↓" : "↑") +
                  "</button>"
              : v,
            i,
            "th",
          ),
        )
        .join("") +
      "</tr>";
    const size = Number($("page-size").value),
      pages = Math.max(1, Math.ceil(filtered.length / size));
    page = Math.min(page, pages);
    $("records").querySelector("tbody").innerHTML =
      filtered
        .slice((page - 1) * size, page * size)
        .map((r) => {
          const date = new Date(r.created_at),
            valid = !Number.isNaN(date.valueOf());
          const dateText = valid
            ? date.toLocaleDateString("ru-RU")
            : esc(r.created_at || "—");
          const button = `<button class="button" data-open="${esc(r.id)}">Открыть</button>`;
          const values = supplies
            ? [
                dateText,
                esc(r.number),
                esc(r.title),
                esc(r.comment),
                number(r.position_count),
                number(r.total_quantity),
                labels[r.status],
                esc(r.created_by),
                button,
              ]
            : [
                dateText,
                valid ? date.toLocaleTimeString("ru-RU") : "—",
                labels[r.source_type] || "Архивная запись",
                esc(r.title),
                esc(r.comment),
                esc(r.brand),
                esc(r.category),
                image(r.image_url),
                `<div class="name">${esc(r.name)}</div>`,
                esc(r.article),
                number(r.stock_before),
                `<span class="positive">+${number(r.quantity)}</span>`,
                number(r.stock_after),
                esc(r.user_name),
                button,
              ];
          return "<tr>" + values.map((v, i) => cell(v, i)).join("") + "</tr>";
        })
        .join("") ||
      `<tr><td colspan="${columns.length}">Записей пока нет</td></tr>`;
    $("page-info").textContent =
      `Страница ${page} из ${pages} · ${filtered.length} записей`;
    $("previous").disabled = page <= 1;
    $("next").disabled = page >= pages;
  }
  async function openSupply(id) {
    current = id ? await api("supplies/" + encodeURIComponent(id)) : null;
    items = current ? current.items.map((i) => ({ ...i })) : [];
    $("title").value = current?.title || "";
    $("comment").value = current?.comment || "";
    $("supply-heading").textContent = current
      ? `Поставка ${current.number}`
      : "Новая поставка";
    $("supply-meta").textContent = current
      ? `${labels[current.status]} · Создана ${current.created_at} · ${current.created_by}${current.posted_at ? " · Проведена " + current.posted_at + " · " + current.posted_by : ""}`
      : "";

    message("", true);
    renderItems();
    if (!$("supply-dialog").open) $("supply-dialog").showModal();
  }
  function renderItems() {
    const posted = current?.status === "posted";
    $("add-item").hidden =
      !current || !["draft", "posted"].includes(current.status);
    $("title").disabled = posted;
    $("comment").disabled = posted;
    $("draft-actions").hidden = posted;
    $("save-first-hint").hidden = Boolean(current);
    for (const id of ["save-supply", "post-supply", "delete-supply"])
      $(id).hidden = posted || (id === "delete-supply" && !current);
    $("items").querySelector("tbody").innerHTML = items
      .map(
        (i, index) =>
          `<tr><td>${image(i.image_url)}</td><td>${esc(i.name)}</td><td>${esc(i.article)}</td><td>${esc(i.brand)}</td><td>${number(i.stock_before ?? i.stock)}</td><td>${posted ? number(i.quantity) : `<input type="number" min="1" max="2147483647" step="1" data-quantity="${index}" ${busy ? "disabled" : ""} aria-label="Приход ${esc(i.name)}" value="${esc(i.quantity)}">`}</td><td data-after="${index}">${number(posted ? i.stock_after : (i.stock_before ?? i.stock) + Number(i.quantity))}</td><td>${posted ? "" : `<button class="button" data-remove="${index}" aria-label="Удалить ${esc(i.name)}">×</button>`}</td></tr>`,
      )
      .join("");
    $("supply-totals").textContent =
      `Позиций: ${items.length} · Единиц: ${number(items.reduce((sum, i) => sum + Number(i.quantity), 0))}`;
  }
  async function save() {
    if (!$("title").value.trim()) throw new Error("Укажите название поставки.");
    if (items.some((i) => !Number.isInteger(i.quantity) || i.quantity <= 0))
      throw new Error("Количество должно быть целым положительным числом.");
    if (!current)
      current = await api("supplies", {
        method: "POST",
        body: JSON.stringify({
          title: $("title").value,
          comment: $("comment").value,
        }),
      });
    current = await api("supplies/" + encodeURIComponent(current.id), {
      method: "PATCH",
      body: JSON.stringify({
        title: $("title").value,
        comment: $("comment").value,
        items: items.map((i) => ({
          product_id: Number(i.product_id ?? i.id),
          quantity: i.quantity,
        })),
      }),
    });
    items = current.items;
    return current;
  }
  document.querySelectorAll("[data-tab]").forEach(
    (b) =>
      (b.onclick = () =>
        action(async () => {
          tab = b.dataset.tab;
          restoreColumns();
          history.replaceState(null, "", "?tab=" + tab);
          await load();
        })),
  );
  $("filters").onsubmit = (e) => {
    e.preventDefault();
    page = 1;
    render();
  };
  $("filters").onreset = () =>
    setTimeout(() => {
      page = 1;
      render();
    }, 0);
  $("columns").onchange = (e) => {
    if (e.target.dataset.col !== undefined) {
      const n = Number(e.target.dataset.col);
      e.target.checked ? hidden.delete(n) : hidden.add(n);
      try {
        localStorage.setItem(storageKey(), JSON.stringify([...hidden]));
      } catch {
        /* Storage is optional. */
      }
      render();
    }
  };
  $("page-size").onchange = () => {
    page = 1;
    render();
  };
  $("previous").onclick = () => {
    page--;
    render();
  };
  $("next").onclick = () => {
    page++;
    render();
  };
  $("new-supply").onclick = () => action(() => openSupply());
  $("close-supply").onclick = () => $("supply-dialog").close();
  $("close-source").onclick = () => $("source-dialog").close();
  $("records").onclick = (e) => {
    if (e.target.closest("[data-sort-date]")) {
      sortDirection = sortDirection === "desc" ? "asc" : "desc";
      render();
      return;
    }
    const b = e.target.closest("[data-open]");
    if (!b) return;
    const r = rows.find((i) => String(i.id) === b.dataset.open);
    action(async () => {
      if (tab === "supplies" || r.source_type === "supply") {
        await openSupply(tab === "supplies" ? r.id : r.source_id);
        return;
      }
      $("source-content").innerHTML =
        `<p>${esc(r.title)}</p><p>${esc(r.name)} · ${esc(r.article)}</p><p>${esc(r.comment)}</p><p>Было: ${number(r.stock_before)} · Изменение: +${number(r.quantity)} · Стало: ${number(r.stock_after)}</p>` +
        (r.source_type === "sale_cancellation"
          ? `<a href="/sales?source=all&q=${encodeURIComponent(r.sale_number || r.source_id)}">Открыть продажу</a>`
          : r.source_type === "legacy_excel"
            ? `<a href="/products/receipts/${encodeURIComponent(r.source_id)}">Открыть исходный Excel-приход</a>`
            : "");
      $("source-dialog").showModal();
    });
  };
  $("items").oninput = (e) => {
    const n = e.target.dataset.quantity;
    if (n === undefined) return;
    items[n].quantity = Number(e.target.value);
    $("items").querySelector(`[data-after="${n}"]`).textContent = number(
      (items[n].stock_before ?? items[n].stock) + items[n].quantity,
    );
    $("supply-totals").textContent =
      `Позиций: ${items.length} · Единиц: ${number(items.reduce((s, i) => s + i.quantity, 0))}`;
  };
  $("items").onclick = (e) => {
    const b = e.target.closest("[data-remove]");
    if (b) {
      items.splice(Number(b.dataset.remove), 1);
      renderItems();
    }
  };
  let addition = null;
  let pickerProduct = null,
    searchTimer = null,
    searchController = null,
    searchVersion = 0;
  function selectedProduct() {
    return pickerProduct;
  }
  function cancelSearch() {
    clearTimeout(searchTimer);
    searchVersion++;
    searchController?.abort();
  }
  async function searchProducts() {
    const version = searchVersion;
    const controller = new AbortController();
    searchController = controller;
    const parameters = new URLSearchParams({ type: "product", limit: "200" });
    const query = $("supply-product-search").value.trim();
    if (query) parameters.set("q", query);
    $("supply-search-status").textContent =
      "Ищем в " + $("supply-product-source").value.toUpperCase() + "…";
    try {
      const bitrix = $("supply-product-source").value === "bitrix";
      const products = bitrix
        ? await window.ERPProductPicker.bitrix.search(query, controller.signal)
        : await window.ERPProductPicker.request(
            "/api/v1/catalog/options?" + parameters,
            { signal: controller.signal },
          );
      if (version !== searchVersion || controller.signal.aborted) return;
      window.ERPProductPicker.results(
        $("supply-product-results"),
        products.map((p) => ({
          ...p,
          source_label: bitrix ? "Bitrix" : "Уже в ERP",
        })),
        async (p) => {
          if (busy || addition) return;
          cancelSearch();
          const selectionVersion = searchVersion;
          pickerProduct = null;
          updateAddition();
          try {
            if (bitrix) {
              p = await window.ERPProductPicker.bitrix.preview(p.bitrix_id);
              if (selectionVersion !== searchVersion) return;
              if (p.ambiguous)
                throw new Error(
                  "Найдено несколько карточек ERP. Требуется ручное сопоставление.",
                );
              p = {
                ...p,
                stock: undefined,
                source_label: p.existing ? "Уже в ERP" : "Bitrix",
                id: p.existing?.id,
              };
              for (const kind of ["brand", "category"]) {
                const select = $("supply-" + kind);
                select.replaceChildren(new Option("Выберите значение", ""));
                if (!p.existing && p[kind] && !p[kind + "_id"]) {
                  const options = await window.ERPProductPicker.request(
                    "/api/v1/catalog/options?type=" + kind + "&limit=200",
                  );
                  if (selectionVersion !== searchVersion) return;
                  options.forEach((option) =>
                    select.add(new Option(option.name, option.id)),
                  );
                }
                select.hidden = Boolean(
                  p.existing || !p[kind] || p[kind + "_id"],
                );
                document.querySelector(
                  'label[for="supply-' + kind + '"]',
                ).hidden = select.hidden;
              }
            }
            if (selectionVersion !== searchVersion) return;
            pickerProduct = p;
            window.ERPProductPicker.highlight(
              $("supply-product-results"),
              bitrix ? p.bitrix_id : p.id,
            );
            $("supply-search-status").textContent = p.source_label;
            $("add-item-message").hidden = true;
            updateAddition();
          } catch (error) {
            if (selectionVersion === searchVersion)
              $("supply-search-status").textContent = error.message;
          }
        },
        bitrix ? "bitrix_id" : "id",
      );
      $("supply-search-status").textContent = products.length
        ? "Показано товаров: " + products.length
        : bitrix
          ? "Товары не найдены. Введите название, артикул или Bitrix ID."
          : "Товар не найден в ERP. Выберите источник Bitrix для поиска и импорта.";
    } catch (error) {
      if (error.name === "AbortError" || version !== searchVersion) return;
      $("supply-search-status").textContent = error.message;
    }
  }
  $("supply-product-search").oninput = () => {
    cancelSearch();
    pickerProduct = null;
    $("supply-product-results").replaceChildren();
    updateAddition();
    $("supply-search-status").textContent =
      "Ищем в " + $("supply-product-source").value.toUpperCase() + "…";
    searchTimer = setTimeout(searchProducts, 300);
  };
  $("supply-product-source").onchange = $("supply-product-search").oninput;
  function updateAddition() {
    $("supply-product-source").disabled = busy || Boolean(addition);
    $("supply-taxonomy").hidden =
      !pickerProduct?.bitrix_id || Boolean(pickerProduct?.existing);
    $("add-quantity").disabled = busy || Boolean(addition);
    $("supply-product-search").disabled = busy || Boolean(addition);
    $("quantity-minus").disabled =
      busy || Boolean(addition) || Number($("add-quantity").value) <= 1;
    $("quantity-plus").disabled =
      busy ||
      Boolean(addition) ||
      Number($("add-quantity").value) >= 2147483647;
    $("supply-selection-empty").hidden = Boolean(selectedProduct() || addition);
    $("supply-selection-controls").hidden = !selectedProduct() && !addition;
    $("confirm-add-item").disabled =
      busy ||
      (!addition && (!selectedProduct() || !$("add-quantity").validity.valid));
    $("confirm-add-item").textContent = addition
      ? "Проверить предыдущую операцию"
      : pickerProduct?.bitrix_id && !pickerProduct?.id
        ? "Импортировать и добавить"
        : "Добавить в поставку";
    if (addition) {
      $("selected-product").textContent =
        `${addition.name}: +${addition.payload.quantity} шт.`;
      $("duplicate-confirmation").textContent =
        "Проверка результата предыдущей операции.";
      return;
    }
    const p = selectedProduct(),
      q = Number($("add-quantity").value);
    window.ERPProductPicker.preview($("selected-product"), p);
    const existing = items.find((i) => Number(i.product_id) === Number(p?.id));
    $("duplicate-confirmation").textContent = existing
      ? `Этот товар уже есть в поставке. Сейчас: ${number(existing.quantity)} шт. Добавить ещё ${number(q)} шт.?`
      : current?.status === "draft"
        ? "Товар добавится в черновик. Остаток изменится при проведении."
        : "";
  }
  $("add-quantity").oninput = updateAddition;
  for (const [id, change] of [
    ["quantity-minus", -1],
    ["quantity-plus", 1],
  ]) {
    $(id).onclick = () => {
      const q = Number($("add-quantity").value);
      if (Number.isInteger(q))
        $("add-quantity").value = Math.max(1, Math.min(2147483647, q + change));
      updateAddition();
    };
  }
  $("add-item").onclick = () => {
    // Persist an uncertain request across refresh. Retrying keeps its original payload and key.
    if (
      current.status === "draft" &&
      (JSON.stringify(items) !== JSON.stringify(current.items) ||
        $("title").value !== current.title ||
        $("comment").value !== current.comment)
    ) {
      message("Сначала сохраните изменения черновика.", true);
      return;
    }
    cancelSearch();
    pickerProduct = null;
    $("supply-product-search").value = "";
    $("supply-product-results").replaceChildren();
    const saved = sessionStorage.getItem("supply-add:" + current.id);
    addition = saved ? JSON.parse(saved) : null;
    $("add-item-message").hidden = !addition;
    $("add-item-message").textContent = addition
      ? "Повторите подтверждение предыдущей операции. Повторный приход исключён."
      : "";
    $("confirm-add-item").textContent = addition
      ? "Проверить предыдущую операцию"
      : "Добавить в поставку";
    $("add-quantity").value = addition?.payload.quantity || 1;
    updateAddition();
    $("add-item-dialog").showModal();
    if (!addition) {
      searchProducts();
      $("supply-product-search").focus();
    }
  };
  $("close-add-item").onclick = $("cancel-add-item").onclick = () => {
    if (!busy) $("add-item-dialog").close();
  };
  $("add-item-dialog").addEventListener("cancel", (event) => {
    if (busy) event.preventDefault();
  });
  $("add-item-dialog").addEventListener("close", () => {
    cancelSearch();
    pickerProduct = null;
  });
  $("add-item-form").onsubmit = async (event) => {
    event.preventDefault();
    if (busy) return;
    busy = true;
    updateAddition();
    $("confirm-add-item").disabled = true;
    try {
      const p = selectedProduct();
      if (!addition) {
        const quantity = Number($("add-quantity").value);
        if (!p) throw new Error("Выберите товар из каталога ERP.");
        if (
          !Number.isInteger(quantity) ||
          quantity <= 0 ||
          quantity > 2147483647
        )
          throw new Error("Количество должно быть целым положительным числом.");
        if (p.bitrix_id) {
          updateAddition();
          const imported = await window.ERPProductPicker.bitrix.import(
            p.bitrix_id,
            {
              supply_id: current.id,
              brand_id: p.brand_id || $("supply-brand").value || null,
              category_id: p.category_id || $("supply-category").value || null,
            },
          );
          p.id = imported.erp_product_id;
          p.cardResolved = true;
        }
        addition = {
          cardResolved: Boolean(p.cardResolved),
          key:
            crypto.randomUUID?.() ||
            Array.from(crypto.getRandomValues(new Uint8Array(16)), (byte) =>
              byte.toString(16).padStart(2, "0"),
            ).join(""),
          name: p.name,
          payload: { product_id: Number(p.id), quantity },
        };
        sessionStorage.setItem(
          "supply-add:" + current.id,
          JSON.stringify(addition),
        );
      }
      updateAddition();
      const result = await api(
        "supplies/" + encodeURIComponent(current.id) + "/items",
        {
          method: "POST",
          headers: { "Idempotency-Key": addition.key },
          body: JSON.stringify(addition.payload),
        },
      );
      const success = `${addition.name} добавлен в поставку ${result.number}: +${addition.payload.quantity} шт.${result.status === "draft" ? " Остаток изменится при проведении." : ""}`;
      sessionStorage.removeItem("supply-add:" + current.id);
      addition = null;
      current = result;
      items = result.items.map((item) => ({ ...item }));
      $("add-item-dialog").close();
      renderItems();
      message(success, true);
      await load();
    } catch (error) {
      const cardResolved =
        addition?.cardResolved || pickerProduct?.cardResolved;
      if ([403, 422].includes(error.status)) {
        sessionStorage.removeItem("supply-add:" + current.id);
        addition = null;
      }
      updateAddition();
      $("add-item-message").textContent =
        (cardResolved
          ? "Товар сохранён в ERP, но добавление в поставку не подтверждено. "
          : "") + error.message;
      $("add-item-message").hidden = false;
    } finally {
      busy = false;
      renderItems();
      updateAddition();
    }
  };
  $("save-supply").onclick = () =>
    action(async () => {
      await save();
      await load();
      await openSupply(current.id);
      message("Черновик сохранён. Остаток не изменён.", true);
    }, true);
  $("post-supply").onclick = () =>
    action(async () => {
      await save();
      const id = current.id;
      await api("supplies/" + encodeURIComponent(id) + "/post", {
        method: "POST",
      });
      await load();
      await openSupply(id);
      message("Поставка проведена. Остатки обновлены.", true);
    }, true);
  $("delete-supply").onclick = () =>
    action(async () => {
      await api("supplies/" + encodeURIComponent(current.id), {
        method: "DELETE",
      });
      $("supply-dialog").close();
      await load();
      message("Черновик удалён. Остаток не изменён.");
    }, true);
  $("excel").onchange = () =>
    action(async () => {
      const file = $("excel").files[0];
      if (!file) return;
      const data = new FormData();
      data.append("file", file);
      const supply = await api("excel", { method: "POST", body: data });
      tab = "supplies";
      await load();
      await openSupply(supply.id);
      message(
        "Excel загружен в черновик. Проверьте позиции и проведите поставку.",
        true,
      );
      $("excel").value = "";
    });
  action(load);
})();
