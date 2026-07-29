import { z } from 'zod';

import { apiRequest } from './client';

describe('apiRequest', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('uses the same-origin session and validates a successful envelope', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          data: { id: 42, name: 'Тестовый товар' },
          meta: { request_id: 'request-1' },
          error: null,
        }),
        {
          headers: { 'Content-Type': 'application/json' },
          status: 200,
        },
      ),
    );
    vi.stubGlobal('fetch', fetchMock);

    const result = await apiRequest('/products/42', z.object({ id: z.number(), name: z.string() }));

    expect(result.data.name).toBe('Тестовый товар');
    expect(result.meta.request_id).toBe('request-1');
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/v1/products/42',
      expect.objectContaining({ credentials: 'same-origin' }),
    );
  });
});
