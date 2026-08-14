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
| Вкладки | `.erp-section-tabs` используется на товарах и журнале; продажи имеют собственный слой tabs | Единого DOM-контракта для всех страниц нет |
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

Связанные документы: [UX](../ux/README.md),
[Definition of Done](../quality/definition-of-done.md),
[реестр](../document-register.md).
