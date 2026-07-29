import { z } from 'zod';

import { apiRequest, jsonRequestInit } from '../../api/client';
import {
  receiptCatalogSchema,
  receiptListSchema,
  receiptSchema,
  receiptsMetaSchema,
  type ReceiptFormValues,
} from './schemas';

export async function fetchReceipts(searchParams: URLSearchParams) {
  const query = searchParams.toString();
  const envelope = await apiRequest(
    `/receipts${query ? `?${query}` : ''}`,
    receiptListSchema,
  );
  return {
    receipts: envelope.data,
    meta: receiptsMetaSchema.parse(envelope.meta),
  };
}

export async function fetchReceiptCatalog(query = '') {
  const params = new URLSearchParams({ limit: '200' });
  if (query) params.set('q', query);
  return (await apiRequest(`/receipts/catalog?${params}`, receiptCatalogSchema)).data;
}

export async function createReceipt(values: ReceiptFormValues) {
  return (await apiRequest('/receipts', receiptSchema, jsonRequestInit('POST', values))).data;
}

export async function updateReceipt(id: string, values: ReceiptFormValues) {
  return (
    await apiRequest(`/receipts/${id}`, receiptSchema, jsonRequestInit('PATCH', values))
  ).data;
}

export async function deleteReceipt(id: string) {
  return (
    await apiRequest(
      `/receipts/${id}`,
      z.object({ id: z.string(), deleted: z.boolean() }),
      jsonRequestInit('DELETE'),
    )
  ).data;
}
