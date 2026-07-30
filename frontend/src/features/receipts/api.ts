import { z } from 'zod';

import { apiRequest, jsonRequestInit } from '../../api/client';
import {
  receiptListSchema,
  receiptSchema,
  receiptsMetaSchema,
  type ReceiptFormValues,
} from './schemas';

export async function fetchReceipts(searchParams: URLSearchParams) {
  const query = searchParams.toString();
  const envelope = await apiRequest(`/receipts${query ? `?${query}` : ''}`, receiptListSchema);
  return {
    receipts: envelope.data,
    meta: receiptsMetaSchema.parse(envelope.meta),
  };
}

export async function createReceipt(values: ReceiptFormValues) {
  return (
    await apiRequest(
      '/receipts',
      receiptSchema,
      jsonRequestInit('POST', {
        ...values,
        idempotency_key: crypto.randomUUID(),
      }),
    )
  ).data;
}

export async function updateReceipt(id: string, values: ReceiptFormValues) {
  return (
    await apiRequest(
      `/receipts/${id}`,
      receiptSchema,
      jsonRequestInit('PATCH', {
        ...values,
        idempotency_key: crypto.randomUUID(),
      }),
    )
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
