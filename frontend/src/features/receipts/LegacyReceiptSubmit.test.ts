/// <reference types="node" />

import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

import userEvent from '@testing-library/user-event';

type ReceiptSubmitApi = {
  buildCreatePayload: (form: HTMLFormElement, productId: string) => FormData;
  errorMessage: (response: Response, payload: unknown) => string;
  request: (
    form: HTMLFormElement,
    endpoint: string,
    method: string,
    body: FormData | object,
    idempotencyKey: string,
  ) => Promise<unknown>;
  submissionKey: (form: HTMLFormElement) => string;
  successUrl: (submitMode: string, payload: unknown) => string;
};

function loadReceiptSubmit(): ReceiptSubmitApi {
  const source = readFileSync(resolve(process.cwd(), '../app/static/js/receipt-submit.js'), 'utf8');
  window.eval(source);
  return (window as typeof window & { VechasuReceiptSubmit: ReceiptSubmitApi })
    .VechasuReceiptSubmit;
}

function receiptForm() {
  document.body.innerHTML = `
    <form id="receiptForm">
      <input name="csrf_token" value="csrf">
      <input name="receipt_date" value="2026-07-30">
      <input name="quantity" value="2">
      <input name="product_image" type="file">
      <textarea name="note">Поставка</textarea>
    </form>
  `;
  return document.querySelector<HTMLFormElement>('#receiptForm')!;
}

describe('legacy receipt submission', () => {
  afterEach(() => vi.unstubAllGlobals());

  it('sends the real PNG file through multipart form data', async () => {
    const api = loadReceiptSubmit();
    const form = receiptForm();
    const imageInput = form.querySelector<HTMLInputElement>('[name="product_image"]')!;
    const image = new File([new Uint8Array([0x89, 0x50, 0x4e, 0x47])], 'watch.png', {
      type: 'image/png',
    });
    await userEvent.upload(imageInput, image);

    const payload = api.buildCreatePayload(form, '10876');
    const positions = JSON.parse(String(payload.get('positions')));
    const uploaded = payload.get('product_image');

    expect(positions).toEqual([{ product_id: '10876', quantity: '2' }]);
    expect(uploaded).toBeInstanceOf(File);
    expect((uploaded as File).name).toBe('watch.png');

    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          data: { id: 'receipt-1' },
          meta: { image_message: 'Фото товара добавлено.' },
          error: null,
        }),
        { status: 201, headers: { 'Content-Type': 'application/json' } },
      ),
    );
    vi.stubGlobal('fetch', fetchMock);

    await api.request(form, '/api/v1/receipts', 'POST', payload, 'receipt-once');
    const requestInit = fetchMock.mock.calls[0][1] as RequestInit;
    const headers = requestInit.headers as Record<string, string>;
    expect(requestInit.body).toBe(payload);
    expect(headers['Content-Type']).toBeUndefined();
    expect(headers['Idempotency-Key']).toBe('receipt-once');
  });

  it('shows a safe concrete API error and masks server details', () => {
    const api = loadReceiptSubmit();
    expect(
      api.errorMessage(new Response(null, { status: 422 }), {
        code: 'RECEIPT_VALIDATION_FAILED',
        message: 'Товар не найден.',
      }),
    ).toBe('Товар не найден.');
    expect(
      api.errorMessage(new Response(null, { status: 500 }), {
        message: 'Traceback: secret implementation detail',
      }),
    ).toBe('Ошибка сервера при сохранении прихода.');
    expect(api.errorMessage(new Response(null, { status: 413 }), null)).toBe(
      'Файл слишком большой. Максимальный размер — 3 МБ.',
    );
  });

  it('keeps one idempotency key and reopens only the next-receipt flow', () => {
    const api = loadReceiptSubmit();
    const form = receiptForm();
    expect(api.submissionKey(form)).toBe(api.submissionKey(form));

    const nextUrl = api.successUrl('create_next', {
      meta: { image_message: 'Фото товара добавлено.' },
    });
    const closeUrl = api.successUrl('close', { meta: {} });
    expect(nextUrl).toContain('open_receipt_modal=1');
    expect(new URL(nextUrl, 'https://erp.test').searchParams.get('message')).toContain(
      'Форма готова для следующего прихода.',
    );
    expect(closeUrl).not.toContain('open_receipt_modal');
  });
});
