import { apiRequest, formDataRequestInit, jsonRequestInit } from '../../api/client';
import { z } from 'zod';
import {
  productListSchema,
  productSchema,
  productsMetaSchema,
  type ProductFormValues,
} from './schemas';

export async function fetchProducts(searchParams: URLSearchParams, signal?: AbortSignal) {
  const query = searchParams.toString();
  const envelope = await apiRequest(`/products${query ? `?${query}` : ''}`, productListSchema, {
    signal,
  });
  return {
    products: envelope.data,
    meta: productsMetaSchema.parse(envelope.meta),
  };
}

export async function createProduct(values: ProductFormValues, image: File | null = null) {
  const payload = new FormData();
  for (const [key, value] of Object.entries(values)) {
    payload.append(key, value === null ? '' : String(value));
  }
  if (image) payload.append('product_image', image, image.name);
  return (await apiRequest('/products', productSchema, formDataRequestInit('POST', payload))).data;
}

export async function updateProduct(id: number, values: ProductFormValues) {
  return (await apiRequest(`/products/${id}`, productSchema, jsonRequestInit('PATCH', values)))
    .data;
}

export async function deleteProduct(id: number) {
  return (
    await apiRequest(
      `/products/${id}`,
      z.object({ id: z.number(), deleted: z.boolean() }),
      jsonRequestInit('DELETE'),
    )
  ).data;
}

export async function bulkUpdateProducts(
  ids: number[],
  changes: Partial<Pick<ProductFormValues, 'brand_id' | 'category_id' | 'cell'>>,
) {
  return (
    await apiRequest(
      '/products/bulk',
      z.object({
        items: z.array(productSchema),
        updated: z.number(),
        errors: z.array(z.object({ id: z.string(), message: z.string() })),
      }),
      jsonRequestInit('PATCH', { ids, changes }),
    )
  ).data;
}

export async function createBrand(name: string) {
  return (
    await apiRequest(
      '/brands',
      z.object({ name: z.string(), count: z.number() }),
      jsonRequestInit('POST', { name }),
    )
  ).data;
}

export async function createCategory(brandId: number, name: string) {
  return (
    await apiRequest(
      '/categories',
      z.object({
        id: z.number(),
        brand_id: z.number(),
        brand: z.string(),
        name: z.string(),
        count: z.number(),
      }),
      jsonRequestInit('POST', { brand_id: brandId, name }),
    )
  ).data;
}
