import { apiRequest, jsonRequestInit } from '../../api/client';
import { z } from 'zod';
import {
  productListSchema,
  productSchema,
  productsMetaSchema,
  type ProductFormValues,
} from './schemas';

export async function fetchProducts(searchParams: URLSearchParams) {
  const query = searchParams.toString();
  const envelope = await apiRequest(
    `/products${query ? `?${query}` : ''}`,
    productListSchema,
  );
  return {
    products: envelope.data,
    meta: productsMetaSchema.parse(envelope.meta),
  };
}

export async function createProduct(values: ProductFormValues) {
  return (
    await apiRequest('/products', productSchema, jsonRequestInit('POST', values))
  ).data;
}

export async function updateProduct(id: number, values: ProductFormValues) {
  return (
    await apiRequest(`/products/${id}`, productSchema, jsonRequestInit('PATCH', values))
  ).data;
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
