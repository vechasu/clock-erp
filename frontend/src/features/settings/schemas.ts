import { z } from 'zod';

export const settingsSchema = z.object({
  company_name: z.string(),
  erp_name: z.string(),
  low_stock_threshold: z.number().int().min(0).max(999),
});

export type Settings = z.infer<typeof settingsSchema>;
