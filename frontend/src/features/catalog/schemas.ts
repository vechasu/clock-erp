import { z } from 'zod';

export const catalogBrandSchema = z.object({
  id: z.number().int().positive(),
  name: z.string(),
  active: z.boolean(),
  product_count: z.number().int().nonnegative(),
});

export const catalogCategorySchema = z.object({
  id: z.number().int().positive(),
  brand_id: z.number().int().positive(),
  name: z.string(),
  active: z.boolean(),
  brand_name: z.string(),
  product_count: z.number().int().nonnegative(),
});

export const catalogProductSchema = z.object({
  id: z.string(),
  product_id: z.string(),
  name: z.string(),
  article: z.string(),
  barcode: z.string(),
  brand_id: z.number().int().positive().nullable(),
  category_id: z.number().int().positive().nullable(),
  brand: z.string(),
  category: z.string(),
  cell: z.string(),
  stock: z.number(),
  stock_display: z.string(),
  active: z.boolean(),
});

export type CatalogBrand = z.infer<typeof catalogBrandSchema>;
export type CatalogCategory = z.infer<typeof catalogCategorySchema>;
export type CatalogProduct = z.infer<typeof catalogProductSchema>;
