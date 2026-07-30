# Повторный аудит PR #116 / `6fa0c2b`

## 1. Компоненты до исправления

На production пользовательские ссылки открывали не React-приложение:

| Раздел | Production-маршрут | Фактическая реализация |
| --- | --- | --- |
| Товары | `/products` → `/warehouse` | Jinja `warehouse.html`, макрос `_catalog_combobox.html`, `catalog-combobox.js`, логика страницы |
| Продажи | `/sales` | Jinja `sales.html`, макрос `_catalog_combobox.html`, `catalog-combobox.js`, логика страницы |
| Приход | `/receipts` | Jinja `receipts.html`, макрос `_catalog_combobox.html`, `catalog-combobox.js`, отдельные `receiptCatalog*`-функции и локальный каталог страницы |

В параллельном React-приложении `/app` все три раздела уже использовали:

- `CatalogCascade`;
- типизированные обёртки `BrandCombobox`, `CategoryCombobox`, `ProductCombobox`;
- базовый `SearchableSelect`;
- единый API `/api/v1/catalog/options`.

Формы `ProductForm`, `SaleForm`, `ReceiptForm` и фильтры соответствующих
страниц импортировали один физический `CatalogCascade` из
`frontend/src/features/catalog/CatalogComboboxes.tsx`.

## 2. Были ли компоненты физически одинаковыми

В React-приложении — да: один `CatalogCascade` и один `SearchableSelect`.

На production-маршрутах — нет. Страницы совместно использовали только
серверный Jinja-макрос и базовый JavaScript выпадающего списка, но загрузка
данных, каскад `Бренд → Категория → Товар`, создание значений и восстановление
выбора оставались отдельной логикой каждой страницы. `/receipts` вообще не
монтировал React-компонент, изменённый в PR #116.

## 3. Файлы PR #116

Исходники и тесты:

- `app/web.py`;
- `frontend/src/features/catalog/CatalogComboboxes.test.tsx`;
- `frontend/src/features/catalog/CatalogComboboxes.tsx`;
- `frontend/src/features/catalog/CatalogCreationModal.tsx`;
- `frontend/src/features/catalog/api.ts`;
- `frontend/src/features/products/ProductForm.tsx`;
- `frontend/src/features/products/ProductsPage.tsx`;
- `frontend/src/features/receipts/ReceiptForm.tsx`;
- `frontend/src/features/receipts/ReceiptsPage.test.tsx`;
- `frontend/src/features/receipts/ReceiptsPage.tsx`;
- `frontend/src/features/receipts/api.ts`;
- `frontend/src/features/receipts/schemas.ts`;
- `frontend/src/features/sales/SaleForm.tsx`;
- `frontend/src/features/sales/SalesPage.tsx`;
- `frontend/src/styles/global.css`;
- `tests/test_catalog_cascade_unification.py`;
- `tests/test_unified_catalog_api.py`.

Собранные production-артефакты:

- удалён `app/static/react/assets/CatalogComboboxes-CGJad9zs.js`;
- добавлен `app/static/react/assets/CatalogComboboxes-DYrR7Tyo.js`;
- удалён `app/static/react/assets/CatalogCreationModal-7VfqAAZI.js`;
- удалён `app/static/react/assets/ProductsPage-BCNOjKwL.js`;
- добавлен `app/static/react/assets/ProductsPage-D18k-JV3.js`;
- удалён `app/static/react/assets/ReceiptsPage-BchT3sbj.js`;
- добавлен `app/static/react/assets/ReceiptsPage-C9sZY7PR.js`;
- `app/static/react/assets/RepairsPage-CHuu3ETr.js` переименован в
  `app/static/react/assets/RepairsPage-B_sE0XCK.js`;
- добавлен `app/static/react/assets/SalesPage--7q8amsr.js`;
- удалён `app/static/react/assets/SalesPage-CoL7gV4I.js`;
- удалён `app/static/react/assets/index-77hJIiYi.css`;
- `app/static/react/assets/index-Cl5Ljy3B.js` переименован в
  `app/static/react/assets/index-Ch4BEN6r.js`;
- добавлен `app/static/react/assets/index-RWzNh2xK.css`;
- `app/static/react/assets/zod-Bo5awiEW.js` переименован в
  `app/static/react/assets/zod-BDgzkv37.js`;
- изменён `app/static/react/index.html`.

## 4. Почему production визуально не изменился

PR #116 изменил React-страницу `/app/receipts`. Основная навигация production
продолжала вести на Flask-маршрут `/receipts`, который рендерил
`app/templates/receipts.html`. Поэтому пользователь видел прежнюю форму,
несмотря на наличие нового React-компонента в собранных файлах.

Исправление подключает React-приложение непосредственно на `/products`,
`/sales` и `/receipts`; `BrowserRouter` поддерживает как основные маршруты,
так и совместимые адреса `/app/*`. Старые серверные страницы доступны только
в `TESTING`-режиме для существующих unit-тестов; отдельный интеграционный тест
включает production-маршрутизацию и проверяет общий React-entrypoint.

## 5. Что подтверждали тесты PR #116

Реальное поведение общего компонента подтверждали:

- `CatalogComboboxes.test.tsx`: серверные запросы, поиск с первого символа и
  после каждого изменения, сортированные варианты, клавиатура, мышь, очистка,
  пустое состояние, сброс зависимых полей, создание и немедленный выбор бренда,
  категории и товара;
- `ReceiptsPage.test.tsx`: восстановление общих `brand_id`, `category_id`,
  `product_id` при редактировании и использование ID в фильтрах;
- `test_unified_catalog_api.py`: единые карточки и ID в товарах, продажах и
  приходах, общий складской реестр;
- `test_catalog_cascade_unification.py`: одинаковый импорт физического
  компонента всеми формами и фильтрами.

Недостающей была проверка точки входа: ни один тест не доказывал, что
production-маршрут `/receipts` действительно монтирует React-приложение.
Поэтому компонентные тесты проходили, а пользовательский интерфейс не менялся.
В исправлении добавлена интеграционная проверка, что `/products`, `/sales` и
`/receipts` отдают один и тот же React-entrypoint.
