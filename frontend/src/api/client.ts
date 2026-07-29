import type { ZodType } from 'zod';

import { apiErrorSchema, apiMetaSchema } from '../schemas/api';
import type { ApiEnvelope, ApiError, ApiMeta } from '../types/api';

export class ApiRequestError extends Error {
  readonly status: number;
  readonly details: ApiError | null;

  constructor(status: number, message: string, details: ApiError | null) {
    super(message);
    this.name = 'ApiRequestError';
    this.status = status;
    this.details = details;
  }
}

export async function apiRequest<T>(
  path: string,
  schema: ZodType<T>,
  init: RequestInit = {},
): Promise<ApiEnvelope<T>> {
  const response = await fetch(`/api/v1${path}`, {
    ...init,
    credentials: 'same-origin',
    headers: {
      Accept: 'application/json',
      ...init.headers,
    },
  });
  const payload: unknown = await response.json();

  if (!response.ok) {
    const parsedError = apiErrorSchema.safeParse(payload);
    const details = parsedError.success ? parsedError.data : null;
    throw new ApiRequestError(response.status, details?.message ?? 'Ошибка API', details);
  }

  if (!payload || typeof payload !== 'object' || !('data' in payload)) {
    throw new ApiRequestError(response.status, 'Некорректный ответ API', null);
  }

  const record = payload as Record<string, unknown>;
  const meta: ApiMeta = apiMetaSchema.parse(record.meta ?? {});

  return {
    data: schema.parse(record.data),
    meta,
    error: null,
  };
}
