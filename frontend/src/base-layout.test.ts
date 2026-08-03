import { describe, expect, it } from 'vitest';

import { BASE_LAYOUT_BUILD_MARKER, BASE_LAYOUT_ROUTES } from './base-layout';

describe('server-rendered ERP frontend build', () => {
  it('contains only the four supported application routes', () => {
    expect(BASE_LAYOUT_ROUTES).toEqual([
      '/app/products',
      '/app/sales',
      '/app/receipts',
      '/app/settings',
    ]);
  });

  it('cannot identify itself as the retired Stage 2 shell', () => {
    expect(BASE_LAYOUT_BUILD_MARKER).toBe('server-rendered-base-layout');
  });
});
