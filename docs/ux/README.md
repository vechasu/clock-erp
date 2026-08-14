# Текущее UX-поведение Vechasu ERP

Статус: `current` для commit `975ef2572edfbf3568c5fac430d31f9d79af1d23`.

Здесь зафиксировано поведение, найденное в Flask routes, Jinja и JavaScript.
Это карта фактов, а не предложение унифицировать все страницы.

## Навигация и URL

- `/` перенаправляет на `/overview`; прикладные разделы доступны через
  server-generated sidebar.
- Товары открываются на `/warehouse` и `/app/products`; `/products`
  перенаправляет на `/warehouse` с сохранением query string.
- Продажи, приходы, ремонты и журнал имеют пары `/...` и `/app/...`, но обе
  стороны обслуживаются текущими Jinja-страницами.
- Desktop sidebar сворачивается; mobile до 767 px использует нижнюю навигацию и
  диалог «Ещё». Активный пункт отмечается `aria-current="page"`.
- Настройки могут скрывать пункты навигации, но обязательные server rules
  определяются кодом, а не только UI.

## Query state, поиск и фильтры

Страницы сохраняют состояние в URL, но поддерживают разные наборы параметров:

| Страница | Основные подтверждённые параметры |
| --- | --- |
| Товары `/warehouse` | `view`, `q`, `brand`, `brand_id`, `category`, `category_id`, `cell`, `date_from`, `date_to`, `in_stock`, `sort_by`, `sort_dir`, `page`, `per_page` |
| Продажи `/sales` | `tab`/`source`, `q`, даты, `order_number`, `order_status`, доставка, регион/город, status и catalog filters, `sort`, `sort_dir`, `page`, `per_page` |
| Приходы `/receipts` | `q`, даты, документ, комментарий, brand/category/product text и IDs, status, `sort`, `sort_dir`, `page`, `per_page` |
| Ремонты `/app/repairs` | `view`, `q`, `status`, `type`, `location`, `channel`, `order_link`, `waiting_for`, `control`, `attention`, `page`, `per_page` |
| Журнал `/journal` | entity/action/actor/status/source, `q`, даты и `cursor` |

Общие Jinja-страницы используют `per_page` из набора 25/50/100. API списков
имеют собственный `page_size` и верхние пределы. Поэтому пагинация визуально
сходна, но технически не является одним контрактом для всех API.

Поиск различается: на товарах есть live/partial loading, на других страницах
часть поиска применяет URL-навигацию или локальную фильтрацию. Не следует
считать все поля одинаково debounce/live без проверки конкретного шаблона.

## Каскад Бренд → Категория → Товар

Общие catalog endpoints `/api/v1/catalog/options`, `/api/v1/brands` и category
overviews поддерживают выбор по стабильным ID. Shared combobox нормализует
регистр, пробелы и `ё/е`, умеет prefix search для product options, клавиатурный
выбор, empty/loading/error state и позиционирование внутри modal. Продажи и
приходы используют каскад, но формы и восстановление значений остаются
page-specific; тесты `test_catalog_cascade_unification.py`,
`test_catalog_combobox_browser.py` и `test_sales_product_combobox.py`
подтверждают только покрытые сценарии.

## Таблицы и пагинация

- товары, продажи и приходы используют `.erp-data-table`, но собственные
  колонки, sort whitelist, column settings и client scripts;
- ремонты имеют desktop table и отдельные mobile cards;
- общий `_pagination.html` показывает диапазон, страницы, предыдущую/следующую
  ссылку и выбор размера с сохранением остальных query-параметров;
- журнал использует cursor вместо page numbers.

## Модальные окна и destructive actions

Shared modal shell удерживает focus внутри активного dialog и блокирует Escape
и закрытие кликом по locked overlay. Товары, продажи и приходы используют
модальные формы; ремонты используют drawer. Конкретный способ закрытия и
сохранения остаётся page-specific.

Удаление товара/бренда/категории, отмена/удаление/возврат продажи и действия с
приходом/ремонтом проходят через POST/PATCH/DELETE handlers с серверными
проверками. Наличие confirmation dialog не означает, что операция допустима:
решение принимает backend. CSRF применяется к авторизованным write-сценариям в
соответствии с текущими guards.

## Desktop, mobile и состояния

- shared shell переключается на mobile navigation при 767 px;
- товары, продажи, приходы и ремонты содержат дополнительные собственные
  breakpoints и не имеют одного общего responsive contract;
- loading, empty и error реализованы неравномерно: товары имеют partial-loading,
  ремонты — явные loading/empty/error blocks, таблицы — empty states, формы —
  inline validation, а глобальные изменяющие запросы могут создавать toast;
- отсутствие данных и ошибка загрузки должны различаться, но это нужно проверять
  целевым тестом каждой затронутой страницы.

Связанные документы: [дизайн](../design/README.md),
[продукт](../product/README.md),
[Definition of Done](../quality/definition-of-done.md).
