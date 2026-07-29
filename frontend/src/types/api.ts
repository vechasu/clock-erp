export interface ApiMeta {
  request_id?: string;
  page?: number;
  page_size?: number;
  total?: number;
  pages?: number;
  [key: string]: unknown;
}

export interface ApiError {
  code: string;
  message: string;
  fields?: Record<string, string | string[]>;
  request_id?: string;
}

export interface ApiEnvelope<T> {
  data: T;
  meta: ApiMeta;
  error: null;
}
