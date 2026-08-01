import type { ZodType } from 'zod';

import { apiErrorSchema, apiMetaSchema } from '../schemas/api';
import type { ApiEnvelope, ApiError, ApiMeta } from '../types/api';

let csrfToken = '';

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
  const responseText = await response.text();
  let payload: unknown = null;
  try {
    payload = responseText ? JSON.parse(responseText) : null;
  } catch {
    throw new ApiRequestError(
      response.status,
      response.ok
        ? 'Сервер вернул некорректный ответ'
        : 'Не удалось выполнить запрос. Сервер вернул некорректный ответ.',
      null,
    );
  }

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
  if (typeof meta.csrf_token === 'string') {
    csrfToken = meta.csrf_token;
  }

  return {
    data: schema.parse(record.data),
    meta,
    error: null,
  };
}

export function jsonRequestInit(method: 'POST' | 'PATCH' | 'DELETE', data?: unknown): RequestInit {
  return {
    method,
    headers: {
      'Content-Type': 'application/json',
      ...(csrfToken ? { 'X-CSRF-Token': csrfToken } : {}),
    },
    ...(data === undefined ? {} : { body: JSON.stringify(data) }),
  };
}

export function formDataRequestInit(method: 'POST' | 'PATCH', data: FormData): RequestInit {
  return {
    method,
    headers: csrfToken ? { 'X-CSRF-Token': csrfToken } : {},
    body: data,
  };
}
