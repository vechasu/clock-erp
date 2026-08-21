# Текущая дизайн-система Vechasu ERP

Статус: `current` для commit `975ef2572edfbf3568c5fac430d31f9d79af1d23`.

Описание основано только на `app/static/css/`, shared JavaScript и Jinja-шаблонах
текущего `origin/main`. Незакоммиченные компоненты и CSS из других worktree не
использовались.

## Источники

- `app/static/css/themes.css` — глобальные theme tokens и три темы;
- `app/static/css/erp-components.css` — общие элементы рабочих страниц;
- `app/static/css/sidebar.css` — desktop/mobile application shell;
- `app/static/css/notifications.css` — toast-состояния;
- `app/templates/_sidebar.html`, `_pagination.html`, `_catalog_combobox.html` —
  общие Jinja-примитивы;
- `app/static/js/erp-modal-shell.js`, `catalog-combobox.js`,
  `notifications.js`, `sidebar.js`, `theme.js` — общее поведение компонентов.

## Токены и темы

Поддерживаются `classic`, `bn0024-white` и `klok-green`. `themes.css` определяет
surface/background, text/muted, border, primary/accent, focus, success/warning/
error/info, overlay, shadows, radii, control height, table colors, scrollbar,
font family и font weights. `erp-components.css` добавляет:

- типографическую шкалу заголовков, метрик, controls, таблиц, modal и полей;
- spacing scale 4/8/12/16/20/24 px;
- z-index для dropdown, drawer, modal и notifications;
- быстрый transition и focus ring.

Это действующие CSS-переменные, но не гарантия, что каждый page-specific стиль
уже переведён на них: в шаблонах остаются raw colors и локальные значения.

## Визуальные примитивы

| Область | Подтверждённая реализация | Известная неоднородность |
| --- | --- | --- |
| Цвета и состояния | Theme tokens плюс success/warning/error/info и soft variants | Page-specific CSS местами использует literal colors |
| Типографика | Theme font и ERP type tokens; заголовки крупнее и тяжелее табличного текста | Старые шаблоны задают собственные размеры и веса |
| Отступы и поверхности | ERP spacing scale, surfaces, borders, shadows и card radii | Layout padding и gaps частично остаются локальными |
| Кнопки | Общие `.button`, workspace actions, table actions и destructive coloring | Названия классов и размеры различаются между страницами |
| Поля | `.erp-control`, search input, modal fields, combobox trigger | Некоторые формы используют локальные `.control`/`.field` |
| Таблицы | `.erp-data-table`, sticky/colored head, row hover, actions, numeric alignment | Состав колонок, resizing и mobile-представление page-specific |
| Поиск и фильтры | `.erp-search-input`, filter trigger/count, panels, active-filter chips | Набор фильтров и момент применения различаются |
| Вкладки | Товары, продажи, журнал и ремонт используют общий визуальный контракт `.erp-section-tabs` / `.erp-section-tab`; существующие ссылки, порядок и active-механизмы остаются локальными | Продажи сохраняют собственную scroll-обёртку |
| Модальные окна | `[data-erp-modal-shell]`, dialog/header/body/actions, overlay, focus trap | Часть ремонтов использует drawer; закрытие Escape намеренно блокируется modal shell |
| Уведомления | Глобальный `VechasuNotify` и toast palette | В шаблонах также сохраняются inline notices и field errors |

## Application shell

Desktop shell использует фиксированную sidebar шириной 228 px и collapsed
состояние 72 px. На ширине до 767 px sidebar заменяется нижней навигацией и
диалогом «Ещё». Shell показывает активный пункт, доступность разделов,
пользователя и выход; данные пунктов формируются сервером в `app/web.py`.

## Page header

Подтверждённый общий паттерн — `.erp-workspace-header` с heading и actions. Он
используется товарами, брендами, категориями, продажами, приходами, ремонтами,
журналом и настройками. В `erp-components.css` также есть `.erp-page-header`,
но отдельного React-компонента `PageHeader` в текущем `origin/main` нет.

Для товаров, продаж, журнала и ремонтов общий CSS-контракт дополнительно
фиксирует выравнивание шапки, типографику, отступы и responsive-поведение.
Он меняет только оформление существующей разметки: URL, query-параметры,
порядок вкладок и page-specific механизм активного состояния не унифицируются.

## Состояния интерфейса

- focus-visible оформляется focus ring для shared controls;
- disabled controls и пагинация имеют отдельные состояния;
- success/warning/error/info выводятся toast или notices;
- таблицы и ремонты имеют empty/error состояния;
- товары имеют loading/error state при частичной загрузке;
- модальные формы показывают field errors и pending/disabled controls;
- на mobile таблицы отдельных страниц заменяются карточками либо получают
  отдельный responsive layout.

Снимки в `docs/screenshots/` являются материалами аудитов, а не нормативным
visual baseline, пока владелец не подтвердит обратное.

## Раздел «Ремонт»

`repair.html` не содержит локального CSS: шапка, вкладки, toolbar, controls,
chips, таблица, состояния, мобильные карточки, drawer, confirm и fallback-toast
оформляются в repair-контракте `erp-components.css` через общие ERP tokens.
Таблица прокручивается только внутри `.erp-table-scroll`; на ширине до 900 px
она заменяется карточками, а до 600 px фильтры открываются как нижняя панель и
drawer занимает весь экран. Контракт включает `focus-visible`, disabled и
loading-состояния, возврат фокуса из drawer и роли для status/error сообщений.

Маршруты, query-параметры, имена полей и JavaScript-вызовы repair API не входят
в визуальный контракт и при изменении оформления должны сохраняться.

## Раздел «Журнал»

`journal.html` не содержит локального CSS. Шапка, вкладки, toolbar, controls,
синие chips активных фильтров, timeline-карточки, состояния и drawer описаны
единым journal-контрактом в `erp-components.css` и используют общие ERP tokens.
На desktop страница не создаёт горизонтальный overflow; длинные значения
переносятся внутри карточек и drawer. На ширине до 900 px поиск становится
первым элементом toolbar, а до 600 px фильтры открываются нижней панелью,
карточка показывает событие и метаданные перед временем, drawer занимает экран.

Контракт фиксирует `focus-visible`, disabled, loading, empty и error-состояния,
возврат и удержание фокуса в drawer, `aria-live` сообщения и глобальные
уведомления. Маршруты, read-only API, query-параметры, cursor-пагинация,
порядок событий и данные before/after и инвентаризации остаются неизменными.

## Раздел «Настройки»

`settings.html` остаётся одной формой с единственным основным действием.
Карточки «Оформление», «Компания» и «Склад», поля, подсказки, validation,
уведомления и pending-состояние описаны settings-контрактом в
`erp-components.css`. Локальный CSS страницы и блока приглашений удалён.

Theme selector сохраняет темы `classic`, `klok-green`, `bn0024-white`, ключ
`vechasu-erp-theme-v1` и мгновенное применение. Выбор отражается через
`aria-checked`, поддерживает стрелки клавиатуры и видимые selected, hover,
focus и disabled-состояния. На mobile карточки и темы становятся одноколоночными,
а основное сохранение остаётся видимым над нижней навигацией без горизонтального
overflow. API, CSRF, права, хранилище и приглашения сотрудников не входят в
визуальный контракт и сохраняют прежнее поведение.

## Раздел «Продажи»: рабочий список

Первый этап унификации продаж охватывает только рабочий список. Шапка, вкладки
каналов, четыре KPI, toolbar, period picker, фильтры, синие chips, настройки
столбцов, таблица, строковые действия, пагинация и мобильные карточки используют
общие ERP-примитивы и tokenized sales-list контракт в `erp-components.css`.
Фото товара выводится из уже загруженного каталожного контекста с lazy loading и
нейтральным fallback, не меняя snapshot продажи или источники данных.

Таблица сохраняет серверную пагинацию 25/50/100, sort whitelist, URL-фильтры,
порядок/видимость/ширины столбцов и focus mode. Горизонтальная прокрутка
принадлежит только `.erp-table-scroll`; на mobile используется существующий
карточный режим. Формы добавления и редактирования, возврат, отмена, удаление,
архивирование, CSRF и серверные write-handlers в этот этап не входят.

## Раздел «Заказы»

Шаблон `app/templates/orders.html` использует компактную шапку, режимы
«Список», «Разделение» и «Карточка», а в desktop-режиме «Разделение» — рабочие
колонки 37/63. Фильтры состоят из отдельной строки поиска и строки статуса с
периодом; поиск отправляется по Enter, селекты применяются при изменении.
Карточка сохраняет все операции заказа, а каскад сопоставления на широком
экране остаётся одной строкой «Бренд → Категория → Товар → Сопоставить».

Связанные документы: [UX](../ux/README.md),
[Definition of Done](../quality/definition-of-done.md),
[реестр](../document-register.md).
