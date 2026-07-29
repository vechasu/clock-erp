import { z } from 'zod';

export const receiptPositionSchema = z.object({
  product_id: z.string(),
  product_name: z.string(),
  article: z.string(),
  code: z.string(),
  brand: z.string(),
  category: z.string(),
  cell: z.string(),
  quantity: z.number(),
  purchase_price: z.number(),
  line_total: z.number(),
  stock_before: z.number(),
  stock_after: z.number(),
});

export const receiptSchema = z.object({
  id: z.string(),
  number: z.string(),
  created_at: z.string(),
  receipt_date: z.string(),
  brand: z.string(),
  category: z.string(),
  product_id: z.string(),
  product_name: z.string(),
  note: z.string(),
  status: z.string(),
  status_label: z.string(),
  positions: z.array(receiptPositionSchema),
  positions_count: z.number().int(),
  total_quantity: z.number(),
  total_amount: z.number(),
  moysklad_document_id: z.string(),
  moysklad_document_name: z.string(),
  moysklad_document_url: z.string(),
});

export const receiptListSchema = z.array(receiptSchema);

export const receiptCatalogProductSchema = z.object({
  id: z.string(),
  name: z.string(),
  article: z.string(),
  code: z.string(),
  brand: z.string(),
  category: z.string(),
  cell: z.string(),
  stock: z.number(),
  stock_display: z.string(),
  thumbnail_url: z.string(),
  has_images: z.boolean(),
});

export const receiptCatalogSchema = z.array(receiptCatalogProductSchema);

export const receiptFormSchema = z.object({
  receipt_date: z.string().date('Укажите корректную дату'),
  note: z.string().trim(),
  positions: z
    .array(
      z.object({
        brand: z.string().min(1, 'Выберите бренд'),
        category: z.string().min(1, 'Выберите категорию'),
        product_id: z.string().min(1, 'Выберите товар'),
        quantity: z.coerce.number().positive('Количество должно быть больше нуля'),
        purchase_price: z.coerce.number().min(0, 'Цена не может быть отрицательной'),
      }),
    )
    .min(1, 'Добавьте хотя бы один товар'),
  product_image: z
    .object({
      name: z.string(),
      type: z.string(),
      base64: z.string(),
    })
    .nullable(),
});

export const receiptsMetaSchema = z.object({
  page: z.number().int().positive(),
  page_size: z.number().int().positive(),
  total: z.number().int().nonnegative(),
  pages: z.number().int().nonnegative(),
  csrf_token: z.string(),
  totals: z.object({
    quantity: z.number(),
    amount: z.number(),
  }),
  facets: z.object({
    brands: z.array(z.string()),
    categories: z.array(z.string()),
    statuses: z.array(z.string()),
  }),
  sort_by: z.string(),
  sort_dir: z.string(),
});

export type Receipt = z.infer<typeof receiptSchema>;
export type ReceiptCatalogProduct = z.infer<typeof receiptCatalogProductSchema>;
export type ReceiptFormInput = z.input<typeof receiptFormSchema>;
export type ReceiptFormValues = z.output<typeof receiptFormSchema>;
export type ReceiptsMeta = z.infer<typeof receiptsMetaSchema>;
