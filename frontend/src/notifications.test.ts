// @vitest-environment jsdom
import { beforeEach, expect, test, vi } from 'vitest';
import source from '../../app/static/js/notifications.js?raw';

type JsonValue = Record<string, unknown>;

function response(payload: JsonValue, status = 200) {
  const headers = new Headers({'X-Operation-ID': 'server-operation-123'});
  return {
    ok: status >= 200 && status < 300,
    status,
    headers,
    clone: () => ({json: async () => payload}),
  } as Response;
}

function install(fetchResult: Response) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    void input;
    void init;
    return fetchResult;
  });
  Object.defineProperty(window, 'fetch', {value: fetchMock, writable: true, configurable: true});
  Object.defineProperty(window, 'ERP_NOTIFICATION_CONTEXT', {
    value: {actor: {id: '7', name: 'Максим'}, section: 'Товары'},
    writable: true,
    configurable: true,
  });
  window.eval(source);
  document.dispatchEvent(new Event('DOMContentLoaded'));
  return fetchMock;
}

beforeEach(() => {
  document.body.innerHTML = '<main></main><aside><div class="sidebar-footer"></div></aside>';
  delete (window as unknown as {VechasuNotify?: unknown}).VechasuNotify;
  delete (window as unknown as {NotificationManager?: unknown}).NotificationManager;
  sessionStorage.clear();
  vi.restoreAllMocks();
});

test('manual save reports the real object, initiator, section, time, journal and operation id', async () => {
  const fetchMock = install(response({
    data: {id: 42, name: 'GA-2100'},
    meta: {request_id: 'manual-operation-42', changed: true},
    error: null,
  }));

  await window.fetch('/api/v1/products/42', {method: 'PATCH', body: JSON.stringify({name: 'GA-2100'})});

  const toast = document.querySelector<HTMLElement>('.erp-toast')!;
  expect(toast.dataset.kind).toBe('success');
  expect(toast.dataset.operationId).toBe('manual-operation-42');
  expect(toast.textContent).toContain('Товар «GA-2100» сохранён');
  expect(toast.textContent).toContain('Товары · GA-2100 · Максим');
  expect(toast.textContent).toMatch(/\d{2}:\d{2}:\d{2}/);
  expect(toast.querySelector<HTMLAnchorElement>('a')?.href).toContain('/app/journal?entity_type=product&entity_id=42');
  const sentHeaders = new Headers(fetchMock.mock.calls[0][1]?.headers);
  expect(sentHeaders.get('X-Operation-ID')).toBeTruthy();
});

test('background request without changes and ordinary polling stay silent', async () => {
  install(response({data: {updated: 0}, meta: {changed_count: 0}, error: null}));
  await window.fetch('/api/v1/products/bulk', {method: 'PATCH', headers: {'X-Vechasu-Notify': 'background'}});
  await window.fetch('/api/v1/products?page=1');
  expect(document.querySelector('.erp-toast')).toBeNull();
  expect(document.querySelector('[data-erp-background-activity]')).toBeNull();
});

test('background synchronization with changes is passive and attributed to System', async () => {
  install(response({data: {updated: 5}, meta: {changed_count: 5}, error: null}));
  await window.fetch('/api/v1/products/bulk', {method: 'PATCH', headers: {'X-Vechasu-Notify': 'background'}});
  expect(document.querySelector('.erp-toast')).toBeNull();
  const activity = document.querySelector<HTMLElement>('[data-erp-background-activity]')!;
  expect(activity.textContent).toContain('Обновление товаров завершено · изменено: 5');
  expect(activity.textContent).toContain('Система');
});

test('operation errors are separate and keep the diagnostic id', async () => {
  install(response({code: 'FAILED', message: 'Карточка заблокирована', request_id: 'error-operation-42'}, 409));
  await window.fetch('/api/v1/products/42', {method: 'PATCH'});
  const toast = document.querySelector<HTMLElement>('.erp-toast')!;
  expect(toast.dataset.kind).toBe('error');
  expect(toast.textContent).toContain('Не удалось сохранить товар');
  expect(toast.textContent).toContain('Карточка заблокирована');
});

test('server actor is shown as the other employee without cross-tab success leakage', async () => {
  install(response({
    data: {id: 42, name: 'GA-2100'},
    meta: {changed: true, actor: {id: '9', name: 'Анна'}},
    error: null,
  }));
  window.dispatchEvent(new StorageEvent('storage', {key: 'vechasu.erp.pending-notification.v1'}));
  expect(document.querySelector('.erp-toast')).toBeNull();
  await window.fetch('/api/v1/products/42', {method: 'PATCH'});
  expect(document.querySelector('.erp-toast')?.textContent).toContain('Анна');
});

test('presence heartbeat is explicitly notification-free', async () => {
  const fetchMock = install(response({data: {online_count: 2, users: []}, meta: {}, error: null}));
  await window.fetch('/api/v1/presence/heartbeat', {method: 'POST', headers: {'X-Vechasu-Notify': 'off'}});
  expect(fetchMock).toHaveBeenCalledOnce();
  expect(document.querySelector('.erp-toast')).toBeNull();
});

declare global {
  interface Window {
    ERP_NOTIFICATION_CONTEXT?: {
      actor: {id: string; name: string};
      section: string;
    };
  }
}
