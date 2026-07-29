import { z } from 'zod';

export const saleSchema = z.object({
  id: z.string(),
  sale_type: z.string(),
  sale_type_label: z.string(),
  is_manual: z.boolean(),
  inventory_managed: z.boolean(),
  created_at: z.string(),
  source: z.string(),
  source_key: z.string(),
  order_number: z.string(),
  product_id: z.string(),
  product_name: z.string(),
  barcode: z.string(),
  brand: z.string(),
  category: z.string(),
  quantity: z.number(),
  quantity_display: z.string(),
  net_quantity: z.number(),
  returned_quantity: z.number(),
  return_available_quantity: z.number(),
  returned_at: z.string(),
  return_reason: z.string(),
  unit_price: z.number().nullable(),
  total_amount: z.number().nullable(),
  gross_total_amount: z.number(),
  returned_amount: z.number(),
  order_status: z.string(),
  order_status_label: z.string(),
  is_cancelled: z.boolean(),
  cancelled_at: z.string(),
  track_number: z.string(),
  delivery_method: z.string(),
  delivery_cost: z.number(),
  region: z.string(),
  city: z.string(),
  note: z.string(),
  recipient: z.string(),
  recipient_name: z.string(),
  payment_method: z.string(),
  commission: z.string(),
  commission_amount: z.number(),
  country: z.string(),
  delivery_address: z.string(),
  platform: z.string(),
  invoice_number: z.string(),
  sticker_number: z.string(),
});

export const saleListSchema = z.array(saleSchema);

export const saleCatalogProductSchema = z.object({
  id: z.string(),
  name: z.string(),
  article: z.string(),
  barcode: z.string(),
  brand: z.string(),
  category: z.string(),
  stock: z.number(),
  stock_display: z.string(),
});

export const saleCatalogSchema = z.array(saleCatalogProductSchema);
export const saleLocationsSchema = z.record(z.string(), z.record(z.string(), z.array(z.string())));

export const saleFormSchema = z.object({
  created_at: z.string().date('Укажите корректную дату'),
  source: z.string().trim().min(1, 'Выберите источник'),
  product_id: z.string().min(1, 'Выберите товар'),
  product_name: z.string(),
  quantity: z.coerce.number().int().min(1, 'Минимум 1').max(25, 'Максимум 25'),
  unit_price: z.coerce.number().min(1, 'Цена должна быть не меньше 1 ₽'),
  order_number: z.string().trim(),
  order_status: z.string(),
  track_number: z.string().trim(),
  delivery_method: z.string().trim(),
  delivery_cost: z.coerce.number().min(0),
  country: z.string().trim(),
  region: z.string().trim(),
  city: z.string().trim(),
  delivery_address: z.string().trim(),
  recipient: z.string().trim(),
  recipient_name: z.string().trim(),
  payment_method: z.string().trim(),
  commission: z.string().trim(),
  commission_amount: z.coerce.number().min(0),
  platform: z.string().trim(),
  invoice_number: z.string().trim(),
  sticker_number: z.string().trim(),
  note: z.string().trim(),
});

export const salesMetaSchema = z.object({
  page: z.number().int().positive(),
  page_size: z.number().int().positive(),
  total: z.number().int().nonnegative(),
  pages: z.number().int().nonnegative(),
  csrf_token: z.string(),
  totals: z.object({
    active: z.number(),
    cancelled: z.number(),
    quantity: z.number(),
    revenue: z.number(),
    returned: z.number(),
  }),
  facets: z.object({
    sources: z.array(z.string()),
    brands: z.array(z.string()),
    categories: z.array(z.string()),
    statuses: z.array(z.string()),
  }),
  sort_by: z.string(),
  sort_dir: z.string(),
});

export type Sale = z.infer<typeof saleSchema>;
export type SaleCatalogProduct = z.infer<typeof saleCatalogProductSchema>;
export type SaleLocations = z.infer<typeof saleLocationsSchema>;
export type SaleFormInput = z.input<typeof saleFormSchema>;
export type SaleFormValues = z.output<typeof saleFormSchema>;
