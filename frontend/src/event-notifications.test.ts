// @vitest-environment jsdom
import { beforeEach, expect, test, vi } from 'vitest';
import source from '../../app/static/js/event-notifications.js?raw';

const freshFeed = {
  data: {
    unread: 2,
    preferences: {order_sound: false, task_sound: false, browser_notifications: false},
    items: [
      {id: 2, type: 'task', title: 'Новая задача', message: 'Собрать заказ #21134', metadata: {author: 'Иван', due: '2026-09-01 15:00'}, target_url: '/app/tasks?task=77', created_at: '2026-09-01T10:25:00+00:00', read_at: null, fresh: true},
      {id: 1, type: 'order', title: 'Новый заказ #21134', message: 'Wildberries', metadata: {}, target_url: '/order/wildberries/21134', created_at: '2026-09-01T10:32:00+00:00', read_at: null, fresh: true},
    ],
  },
};

beforeEach(() => {
  document.body.innerHTML = `
    <button data-notification-bell><span data-notification-count hidden></span></button>
    <div data-notification-backdrop hidden></div>
    <aside id="notificationCenter" hidden>
      <button data-notification-close></button>
      <button data-notification-filter="all"></button><button data-notification-filter="order"></button><button data-notification-filter="task"></button>
      <button data-notification-read-all></button><div data-notification-feed></div>
      <button data-notification-settings-toggle></button><div data-notification-settings hidden>
        <input type="checkbox" data-notification-preference="order_sound"><input type="checkbox" data-notification-preference="task_sound"><input type="checkbox" data-notification-preference="browser_notifications">
        <p data-notification-permission-note hidden></p>
      </div>
    </aside>`;
  delete (window as unknown as {__vechasuEventNotificationsInitialized?: boolean}).__vechasuEventNotificationsInitialized;
  Object.defineProperty(window, 'ERP_EVENT_NOTIFICATIONS', {value: {csrf: 'csrf'}, configurable: true});
});

test('fresh events create a stacked toast each and render a personal unread feed', async () => {
  const info = vi.fn();
  Object.defineProperty(window, 'VechasuNotify', {value: {info}, configurable: true});
  const fetchMock = vi.fn(async () => ({ok: true, json: async () => freshFeed}));
  Object.defineProperty(window, 'fetch', {value: fetchMock, configurable: true});
  window.eval(source);

  await vi.waitFor(() => expect(info).toHaveBeenCalledTimes(2));
  expect(document.querySelector('[data-notification-count]')?.textContent).toBe('2');
  expect(document.querySelector('[data-notification-feed]')?.textContent).toContain('Новый заказ #21134');
  expect(document.querySelector('[data-notification-feed]')?.textContent).toContain('Собрать заказ #21134');
  expect(info.mock.calls.map((call) => call[1].operationId)).toEqual([
    'event-notification-1', 'event-notification-2',
  ]);
});

test('browser permission is requested only by explicit settings toggle', async () => {
  Object.defineProperty(window, 'VechasuNotify', {value: {info: vi.fn()}, configurable: true});
  Object.defineProperty(window, 'fetch', {value: vi.fn(async () => ({ok: true, json: async () => ({data: {...freshFeed.data, items: []}})})), configurable: true});
  const requestPermission = vi.fn(async () => 'denied');
  Object.defineProperty(window, 'Notification', {value: {permission: 'default', requestPermission}, configurable: true});
  window.eval(source);
  await vi.waitFor(() => expect(document.querySelector('[data-notification-count]')).not.toBeNull());
  expect(requestPermission).not.toHaveBeenCalled();
  const toggle = document.querySelector<HTMLInputElement>('[data-notification-preference="browser_notifications"]')!;
  toggle.checked = true;
  toggle.dispatchEvent(new Event('change'));
  await vi.waitFor(() => expect(requestPermission).toHaveBeenCalledOnce());
  expect(toggle.checked).toBe(false);
  expect(document.querySelector('[data-notification-permission-note]')?.textContent).toContain('не выдано');
});

declare global {
  interface Window {
    ERP_EVENT_NOTIFICATIONS?: {csrf: string};
    VechasuNotify?: {info: ReturnType<typeof vi.fn>};
  }
}
