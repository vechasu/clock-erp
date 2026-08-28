import { expect, test } from '@playwright/test';

test('owner manages, searches, opens, and protects service credentials', async ({ page }) => {
  const browserErrors: string[] = [];
  page.on('console', (message) => {
    if (message.type() === 'error') browserErrors.push(message.text());
  });
  page.on('pageerror', (error) => browserErrors.push(error.message));

  await page.goto('/app/services', { waitUntil: 'load' });
  await expect(page.getByText('Сервисы пока не добавлены')).toBeVisible();
  await page.getByRole('button', { name: '+ Добавить сервис' }).click();
  const dialog = page.getByRole('dialog');
  await expect(dialog).toBeVisible();
  await dialog.getByLabel('Название сервиса').fill('СДЭК тест');
  await dialog.getByLabel('Ссылка').fill('http://127.0.0.1:5057/login');
  await dialog.getByLabel('Описание').fill('Доставка заказов');
  await dialog.getByLabel('Категория').selectOption('delivery');
  await dialog.locator('[data-account="label"]').fill('Основной аккаунт');
  await dialog.locator('[data-account="login"]').fill('courier-user');
  await dialog.locator('[data-account="password"]').fill('browser-only-secret');
  await dialog.getByRole('button', { name: '+ Добавить аккаунт' }).click();
  await dialog.locator('[data-account="label"]').nth(1).fill('Резервный аккаунт');
  await dialog.getByRole('button', { name: 'Сохранить' }).click();

  const card = page.locator('.service-card', { hasText: 'СДЭК тест' });
  await expect(card).toBeVisible();
  await expect(card.getByText('Основной аккаунт')).toBeVisible();
  await expect(card.getByText('Резервный аккаунт')).toBeVisible();
  await expect(card).not.toContainText('browser-only-secret');
  await expect(card.locator('[data-login]').first()).toHaveText('courier-user');

  await page.getByPlaceholder('Найти Bitrix, СДЭК или хостинг…').fill('ничего');
  await expect(page.getByText('Ничего не найдено')).toBeVisible();
  await page.getByPlaceholder('Найти Bitrix, СДЭК или хостинг…').fill('доставка');
  await expect(card).toBeVisible();
  await page.getByRole('button', { name: 'Продажи' }).click();
  await expect(page.getByText('Ничего не найдено')).toBeVisible();
  await page.getByRole('button', { name: 'Все' }).click();
  await page.getByPlaceholder('Найти Bitrix, СДЭК или хостинг…').fill('');

  await card.getByRole('button', { name: 'Копировать логин' }).click();
  await expect.poll(() => page.evaluate(() => navigator.clipboard.readText())).toBe('courier-user');
  await expect(page.getByText('Логин скопирован')).toBeVisible();

  await page.clock.install();
  const passwordResponse = page.waitForResponse((response) =>
    response.url().includes('/password') && response.request().method() === 'GET',
  );
  await card.getByRole('button', { name: 'Показать пароль' }).click();
  await passwordResponse;
  const password = card.locator('[data-password]').first();
  await expect(password).toHaveText('browser-only-secret');
  await page.clock.fastForward(16_000);
  await expect(password).toHaveText('••••••••••••');
  await expect(password).not.toHaveAttribute('data-value');

  const popupPromise = page.waitForEvent('popup');
  await card.getByRole('button', { name: 'Открыть' }).click();
  const popup = await popupPromise;
  await popup.waitForURL('**/login');
  expect(new URL(popup.url()).pathname).toBe('/login');
  await popup.close();

  await card.getByRole('button', { name: 'Изменить' }).click();
  await dialog.getByLabel('Описание').fill('Обновлённая доставка');
  await dialog.getByRole('button', { name: 'Сохранить' }).click();
  await expect(card).toContainText('Обновлённая доставка');

  page.once('dialog', (confirmation) => confirmation.accept());
  await card.getByRole('button', { name: 'В архив' }).click();
  await expect(page.getByText('Сервисы пока не добавлены')).toBeVisible();
  await page.getByRole('button', { name: 'Показать архив' }).click();
  await expect(page.locator('.service-card', { hasText: 'СДЭК тест' })).toBeVisible();
  expect(browserErrors).toEqual([]);
});

test('services grid reflows without horizontal overflow at 320px', async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 900 });
  await page.goto('/app/services', { waitUntil: 'domcontentloaded' });
  const overflow = await page.locator('html').evaluate((node) => node.scrollWidth > node.clientWidth + 1);
  expect(overflow).toBe(false);
  await page.getByRole('button', { name: '+ Добавить сервис' }).click();
  await expect(page.getByRole('dialog')).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(page.getByRole('dialog')).not.toBeVisible();
});
