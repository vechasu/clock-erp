import { z } from 'zod';

export const productSchema = z.object({
  id: z.number().int().positive(),
  name: z.string(),
  article: z.string(),
  barcode: z.string(),
  brand: z.string(),
  category: z.string(),
  cell: z.string(),
  stock: z.number(),
  stock_display: z.string(),
  created_at: z.number(),
  created_at_display: z.string(),
  thumbnail_url: z.string(),
  gallery: z.array(z.unknown()),
  price_display: z.string(),
  source_url: z.string(),
  match_status: z.string(),
  updated_at: z.string(),
});

export const productListSchema = z.array(productSchema);

export const productFormSchema = z.object({
  name: z.string().trim().min(1, 'Название товара обязательно'),
  article: z.string().trim(),
  brand: z.string().trim(),
  category: z.string().trim(),
  cell: z.string().trim(),
  stock: z.coerce.number().min(0, 'Остаток не может быть отрицательным'),
  stock_reason: z.string().trim(),
});

export const facetSchema = z.object({
  name: z.string(),
  count: z.number(),
});

export const productsMetaSchema = z.object({
  page: z.number().int().positive(),
  page_size: z.number().int().positive(),
  total: z.number().int().nonnegative(),
  pages: z.number().int().nonnegative(),
  csrf_token: z.string(),
  stats: z
    .object({
      positions: z.number(),
      total_stock: z.number(),
      positive_positions: z.number().nullable(),
      zero_positions: z.number().nullable(),
    })
    .passthrough(),
  facets: z.object({
    brands: z.array(facetSchema),
    categories: z.array(facetSchema),
    cells: z.array(
      z
        .object({
          cell: z.string(),
          count: z.number(),
        })
        .passthrough(),
    ),
  }),
  sort_by: z.string(),
  sort_dir: z.string(),
});

export type Product = z.infer<typeof productSchema>;
export type ProductFormInput = z.input<typeof productFormSchema>;
export type ProductFormValues = z.infer<typeof productFormSchema>;
export type ProductsMeta = z.infer<typeof productsMetaSchema>;
