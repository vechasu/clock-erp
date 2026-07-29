import { z } from 'zod';

export const apiMetaSchema = z
  .object({
    request_id: z.string().optional(),
    page: z.number().int().positive().optional(),
    page_size: z.number().int().positive().optional(),
    total: z.number().int().nonnegative().optional(),
    pages: z.number().int().nonnegative().optional(),
  })
  .catchall(z.unknown());

export const apiErrorSchema = z.object({
  code: z.string(),
  message: z.string(),
  fields: z.record(z.string(), z.array(z.string()).or(z.string())).optional(),
  request_id: z.string().optional(),
});
