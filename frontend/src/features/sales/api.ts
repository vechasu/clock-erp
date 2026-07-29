import { z } from 'zod';

import { apiRequest, jsonRequestInit } from '../../api/client';
import {
  saleCatalogSchema,
  saleListSchema,
  saleSchema,
  salesMetaSchema,
  type SaleFormValues,
} from './schemas';

export async function fetchSales(searchParams: URLSearchParams) {
  const query = searchParams.toString();
  const envelope = await apiRequest(`/sales${query ? `?${query}` : ''}`, saleListSchema);
  return {
    sales: envelope.data,
    meta: salesMetaSchema.parse(envelope.meta),
  };
}

export async function fetchSaleCatalog() {
  return (await apiRequest('/sales/catalog?limit=200', saleCatalogSchema)).data;
}

export async function createSale(values: SaleFormValues) {
  return (await apiRequest('/sales', saleSchema, jsonRequestInit('POST', values))).data;
}

export async function updateSale(id: string, values: SaleFormValues) {
  return (await apiRequest(`/sales/${id}`, saleSchema, jsonRequestInit('PATCH', values))).data;
}

export async function deleteSale(id: string) {
  return (
    await apiRequest(
      `/sales/${id}`,
      z.object({ id: z.string(), deleted: z.boolean() }),
      jsonRequestInit('DELETE'),
    )
  ).data;
}

export async function returnSale(id: string, quantity: number, reason: string) {
  return (
    await apiRequest(
      `/sales/${id}/returns`,
      saleSchema,
      jsonRequestInit('POST', { quantity, reason }),
    )
  ).data;
}
