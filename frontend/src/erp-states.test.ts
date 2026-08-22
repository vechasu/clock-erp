// @vitest-environment jsdom
import { expect, test } from 'vitest';
import source from '../../app/static/js/erp-states.js?raw';

function tick() {
  return new Promise<void>((resolveTick) => window.setTimeout(resolveTick, 1));
}

test('ERP pending lifecycle protects valid submits and restores safely', async () => {
  document.body.innerHTML = `
        <form id="valid" method="post" data-erp-pending data-pending-label="Сохраняем…">
            <input required value="ok">
            <button type="submit">Сохранить</button>
        </form>
        <form id="cancelled" method="post" data-erp-pending>
            <button type="submit">Отмена</button>
        </form>
        <form id="invalid" method="post" data-erp-pending>
            <input required>
            <button type="submit">Войти</button>
        </form>
    `;
  window.eval(source);

  const valid = document.querySelector<HTMLFormElement>('#valid')!;
  const validButton = valid.querySelector<HTMLButtonElement>('button')!;
  const first = new SubmitEvent('submit', {
    bubbles: true,
    cancelable: true,
    submitter: validButton,
  });
  valid.dispatchEvent(first);
  const duplicate = new SubmitEvent('submit', {
    bubbles: true,
    cancelable: true,
    submitter: validButton,
  });
  valid.dispatchEvent(duplicate);
  expect(first.defaultPrevented).toBe(false);
  expect(duplicate.defaultPrevented).toBe(true);
  await tick();
  expect(valid.getAttribute('aria-busy')).toBe('true');
  expect(validButton.disabled).toBe(true);
  expect(validButton.textContent).toContain('Сохраняем…');

  const cancelled = document.querySelector<HTMLFormElement>('#cancelled')!;
  cancelled.addEventListener('submit', (event) => event.preventDefault());
  cancelled.dispatchEvent(
    new SubmitEvent('submit', {
      bubbles: true,
      cancelable: true,
      submitter: cancelled.querySelector('button'),
    }),
  );
  await tick();
  expect(cancelled.hasAttribute('aria-busy')).toBe(false);
  expect(cancelled.querySelector('button')!.disabled).toBe(false);

  const invalid = document.querySelector<HTMLFormElement>('#invalid')!;
  invalid.requestSubmit();
  await tick();
  expect(invalid.checkValidity()).toBe(false);
  expect(invalid.hasAttribute('aria-busy')).toBe(false);
  expect(invalid.querySelector('button')!.disabled).toBe(false);

  window.dispatchEvent(new Event('pageshow'));
  expect(validButton.disabled).toBe(false);
  expect(validButton.textContent).toBe('Сохранить');
  expect(valid.hasAttribute('aria-busy')).toBe(false);

  validButton.textContent = 'Динамическое состояние';
  window.dispatchEvent(new Event('pageshow'));
  expect(validButton.textContent).toBe('Динамическое состояние');
  validButton.textContent = 'Сохранить';

  await expect(
    window.VechasuStates.run(validButton, 'Повторяем…', async () => {
      throw new Error('network');
    }),
  ).rejects.toThrow('network');
  expect(validButton.disabled).toBe(false);
  expect(validButton.textContent).toBe('Сохранить');
  expect(validButton.hasAttribute('aria-busy')).toBe(false);
});

declare global {
  interface Window {
    VechasuStates: {
      run: (
        control: HTMLButtonElement,
        label: string,
        operation: () => Promise<unknown>,
      ) => Promise<unknown>;
    };
  }
}
