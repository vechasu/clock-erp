(function () {
    'use strict';
    if (window.__vechasuAnalyticsOwner) return;
    window.__vechasuAnalyticsOwner = true;

    var content = document.getElementById('analyticsContent');
    var form = document.getElementById('analyticsFilters');
    var sectionInput = document.getElementById('analyticsSectionInput');
    if (!content || !form || !sectionInput) return;

    var controller = null;
    var requestId = 0;
    var cache = new Map();
    var hiddenColumns = {};

    function applyColumns() {
        var table = content.querySelector('[data-analytics-table]');
        if (!table) return;
        Object.keys(hiddenColumns).forEach(function (key) {
            var index = Number(key);
            Array.prototype.forEach.call(table.rows, function (row) {
                if (row.cells[index]) row.cells[index].hidden = Boolean(hiddenColumns[key]);
            });
        });
        content.querySelectorAll('[data-column-index]').forEach(function (input) {
            input.checked = !hiddenColumns[input.dataset.columnIndex];
        });
    }

    function syncControls(url) {
        var parsed = new URL(url, window.location.origin);
        Array.prototype.forEach.call(form.elements, function (control) {
            if (!control.name || control.type === 'submit') return;
            var value = parsed.searchParams.get(control.name);
            if (value !== null) control.value = value;
        });
        var section = parsed.searchParams.get('section') || 'summary';
        sectionInput.value = section;
        document.querySelectorAll('[data-analytics-section]').forEach(function (link) {
            var active = link.dataset.analyticsSection === section;
            link.classList.toggle('active', active);
            if (active) link.setAttribute('aria-current', 'page');
            else link.removeAttribute('aria-current');
        });
        var exportLink = document.getElementById('analyticsExport');
        if (exportLink) exportLink.href = '/app/analytics/export.csv?' + parsed.searchParams.toString();
        return parsed;
    }

    function urlFor(section) {
        var params = new URLSearchParams(new FormData(form));
        params.set('section', section || sectionInput.value);
        params.set('page', '1');
        return '/app/analytics?' + params.toString();
    }

    function loading(section) {
        content.setAttribute('aria-busy', 'true');
        content.innerHTML = '<section class="analytics-section analytics-skeleton" data-section="' + section + '"><div class="skeleton-title"></div><div class="skeleton-grid"><i></i><i></i><i></i><i></i></div><p>Загружаем раздел…</p></section>';
    }

    function load(url, push, resetScroll) {
        var parsed = syncControls(url);
        var section = parsed.searchParams.get('section') || 'summary';
        var fragment = '/app/analytics/section?' + parsed.searchParams.toString();
        if (controller) controller.abort();
        controller = new AbortController();
        var currentRequest = ++requestId;
        if (push) history.pushState({}, '', parsed.pathname + parsed.search);
        loading(section);

        var cached = cache.get(fragment);
        var response = cached ? Promise.resolve(cached) : fetch(fragment, {
            headers: {'X-Requested-With': 'XMLHttpRequest'},
            signal: controller.signal
        }).then(function (result) {
            if (!result.ok) throw new Error('HTTP ' + result.status);
            return result.text();
        }).then(function (html) {
            cache.set(fragment, html);
            return html;
        });

        response.then(function (html) {
            if (currentRequest !== requestId) return;
            content.innerHTML = html;
            content.setAttribute('aria-busy', 'false');
            applyColumns();
            if (resetScroll) content.scrollIntoView({block: 'start', behavior: 'auto'});
        }).catch(function (error) {
            if (error.name === 'AbortError' || currentRequest !== requestId) return;
            content.innerHTML = '<div class="erp-error-state" role="alert"><strong>Не удалось загрузить аналитику.</strong><p>Проверьте соединение и повторите действие.</p><button class="erp-button erp-button-primary" type="button" data-analytics-retry>Повторить</button></div>';
            content.setAttribute('aria-busy', 'false');
        });
    }

    document.addEventListener('click', function (event) {
        var tab = event.target.closest('[data-analytics-section]');
        if (tab) {
            event.preventDefault();
            load(urlFor(tab.dataset.analyticsSection), true, true);
            return;
        }
        var page = event.target.closest('[data-analytics-page]');
        if (page && page.href && page.getAttribute('aria-disabled') !== 'true') {
            event.preventDefault();
            load(page.href, true, true);
            return;
        }
        var expand = event.target.closest('[data-table-expand]');
        if (expand) {
            var wrapper = content.querySelector('.analytics-table-wrap');
            if (wrapper) {
                wrapper.classList.toggle('is-expanded');
                expand.textContent = wrapper.classList.contains('is-expanded') ? 'Свернуть таблицу' : 'Развернуть таблицу';
                expand.setAttribute('aria-pressed', wrapper.classList.contains('is-expanded') ? 'true' : 'false');
            }
            return;
        }
        if (event.target.closest('[data-analytics-retry]')) load(window.location.href, false, false);
    });

    form.addEventListener('submit', function (event) {
        event.preventDefault();
        cache.clear();
        load(urlFor(sectionInput.value), true, true);
    });
    document.addEventListener('change', function (event) {
        if (!event.target.matches('[data-column-index]')) return;
        hiddenColumns[event.target.dataset.columnIndex] = !event.target.checked;
        applyColumns();
    });
    document.addEventListener('keydown', function (event) {
        if (event.key !== 'Escape') return;
        var wrapper = content.querySelector('.analytics-table-wrap.is-expanded');
        if (!wrapper) return;
        wrapper.classList.remove('is-expanded');
        var button = content.querySelector('[data-table-expand]');
        if (button) {
            button.textContent = 'Развернуть таблицу';
            button.setAttribute('aria-pressed', 'false');
            button.focus();
        }
    });
    window.addEventListener('popstate', function () { load(window.location.href, false, true); });
}());
