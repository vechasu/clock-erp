import { z } from 'zod';

export const receiptPositionSchema = z.object({
  product_id: z.string(),
  product_name: z.string(),
  article: z.string(),
  code: z.string(),
  brand: z.string(),
  category: z.string(),
  brand_id: z.number().int().positive().nullable().default(null),
  category_id: z.number().int().positive().nullable().default(null),
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
  document_number: z.string(),
  created_at: z.string(),
  receipt_date: z.string(),
  brand: z.string(),
  category: z.string(),
  brand_id: z.number().int().positive().nullable().default(null),
  category_id: z.number().int().positive().nullable().default(null),
  product_id: z.string(),
  product_name: z.string(),
  note: z.string(),
  comment: z.string(),
  status: z.string(),
  status_label: z.string(),
  inventory_managed: z.boolean().default(false),
  positions: z.array(receiptPositionSchema),
  positions_count: z.number().int(),
  total_quantity: z.number(),
  total_amount: z.number(),
  moysklad_document_id: z.string(),
  moysklad_document_name: z.string(),
  moysklad_document_url: z.string(),
});

export const receiptListSchema = z.array(receiptSchema);

export const receiptFormSchema = z.object({
  document_number: z.string().trim().min(1, 'Укажите номер документа').max(120),
  receipt_date: z.string().date('Укажите корректную дату'),
  comment: z.string().trim().max(2000, 'Комментарий не должен превышать 2000 символов'),
  positions: z
    .array(
      z.object({
        brand: z.string().min(1, 'Выберите бренд'),
        category: z.string().min(1, 'Выберите категорию'),
        brand_id: z.number().int().positive('Выберите бренд'),
        category_id: z.number().int().positive('Выберите категорию'),
        product_id: z.string().min(1, 'Выберите товар'),
        quantity: z.coerce
          .number()
          .int('Количество должно быть целым числом')
          .positive('Количество должно быть больше нуля'),
        purchase_price: z.coerce.number().min(0, 'Цена не может быть отрицательной'),
      }),
    )
    .min(1, 'Добавьте хотя бы один товар'),
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
export type ReceiptFormInput = z.input<typeof receiptFormSchema>;
export type ReceiptFormValues = z.output<typeof receiptFormSchema>;
export type ReceiptsMeta = z.infer<typeof receiptsMetaSchema>;
