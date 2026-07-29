import { z } from 'zod';

const historyEventSchema = z.object({
  id: z.string(),
  timestamp: z.string(),
  actor: z.string(),
  action: z.string(),
  field: z.string(),
  old_value: z.string(),
  new_value: z.string(),
  comment: z.string(),
});

const shipmentSchema = z.object({
  id: z.string(),
  direction: z.string(),
  direction_label: z.string(),
  carrier: z.string(),
  track_number: z.string(),
  sent_at: z.string(),
  sent_at_display: z.string(),
  status: z.string(),
  received_at: z.string(),
  received_at_display: z.string(),
});

const attachmentSchema = z.object({
  id: z.string(),
  name: z.string(),
  stored_name: z.string(),
  size: z.number(),
  uploaded_at: z.string(),
  uploaded_by: z.string(),
  url: z.string(),
});

export const repairSchema = z.object({
  id: z.string(),
  repair_number: z.string(),
  created_at: z.string(),
  updated_at: z.string(),
  archived_at: z.string(),
  is_archived: z.boolean(),
  responsible: z.string(),
  status: z.string(),
  status_label: z.string(),
  request_type: z.string(),
  request_type_label: z.string(),
  location: z.string(),
  location_label: z.string(),
  communication_channel: z.string(),
  channel_label: z.string(),
  order_number: z.string(),
  order_source: z.string(),
  order_label: z.string(),
  client_name: z.string(),
  client_phone: z.string(),
  client_email: z.string(),
  client_messenger: z.string(),
  contact: z.string(),
  product_id: z.string(),
  product_name: z.string(),
  brand: z.string(),
  model: z.string(),
  article: z.string(),
  serial_number: z.string(),
  product_url: z.string(),
  product_image_url: z.string(),
  problem: z.string(),
  diagnostic_result: z.string(),
  master_conclusion: z.string(),
  decision: z.string(),
  estimate_cost: z.string(),
  final_cost: z.string(),
  master: z.string(),
  equipment: z.string(),
  request_at: z.string(),
  request_at_display: z.string(),
  customer_sent_at: z.string(),
  accepted_at: z.string(),
  accepted_at_display: z.string(),
  master_handoff_at: z.string(),
  master_handoff_at_display: z.string(),
  repair_completed_at: z.string(),
  returned_at: z.string(),
  due_date: z.string(),
  communication: z.string(),
  internal_comment: z.string(),
  latest_event: z.string(),
  shipments: z.array(shipmentSchema),
  attachments: z.array(attachmentSchema),
  history: z.array(historyEventSchema),
});

export const repairListSchema = z.array(repairSchema);

export const repairCatalogItemSchema = z.object({
  id: z.string(),
  name: z.string(),
  brand: z.string(),
  model: z.string(),
  article: z.string(),
  image_url: z.string(),
  url: z.string(),
  search: z.string(),
});

export const repairCatalogSchema = z.array(repairCatalogItemSchema);

const facetSchema = z.object({
  value: z.string(),
  label: z.string(),
});

export const repairsMetaSchema = z.object({
  page: z.number().int().positive(),
  page_size: z.number().int().positive(),
  total: z.number().int().nonnegative(),
  pages: z.number().int().nonnegative(),
  csrf_token: z.string(),
  stats: z.object({
    active: z.number().int().nonnegative(),
    at_us: z.number().int().nonnegative(),
    at_master: z.number().int().nonnegative(),
    delivery: z.number().int().nonnegative(),
    waiting_payment: z.number().int().nonnegative(),
    archived: z.number().int().nonnegative(),
  }),
  facets: z.object({
    statuses: z.array(facetSchema),
    types: z.array(facetSchema),
    locations: z.array(facetSchema),
    channels: z.array(facetSchema),
  }),
  sort_by: z.string(),
  sort_dir: z.string(),
  view: z.string(),
});

export const repairFormSchema = z.object({
  status: z.string(),
  request_type: z.string(),
  responsible: z.string().trim(),
  order_number: z.string().trim(),
  order_source: z.string(),
  client_name: z.string().trim().min(1, 'Укажите имя клиента'),
  client_phone: z.string().trim(),
  client_email: z.union([z.literal(''), z.string().email('Проверьте адрес почты')]),
  client_messenger: z.string().trim(),
  product_id: z.string(),
  product_name: z.string().trim().min(1, 'Укажите товар'),
  brand: z.string().trim(),
  model: z.string().trim(),
  article: z.string().trim(),
  serial_number: z.string().trim(),
  equipment: z.string().trim(),
  communication_channel: z.string(),
  contact: z.string().trim(),
  problem: z.string().trim().min(1, 'Опишите неисправность'),
  diagnostic_result: z.string().trim(),
  master_conclusion: z.string().trim(),
  decision: z.string().trim(),
  estimate_cost: z.string().trim(),
  final_cost: z.string().trim(),
  master: z.string().trim(),
  location: z.string(),
  request_at: z.string(),
  customer_sent_at: z.string(),
  accepted_at: z.string(),
  master_handoff_at: z.string(),
  repair_completed_at: z.string(),
  returned_at: z.string(),
  due_date: z.string(),
  communication: z.string().trim(),
  internal_comment: z.string().trim(),
  event_comment: z.string().trim(),
});

export type Repair = z.infer<typeof repairSchema>;
export type RepairCatalogItem = z.infer<typeof repairCatalogItemSchema>;
export type RepairFormValues = z.infer<typeof repairFormSchema>;
export type RepairsMeta = z.infer<typeof repairsMetaSchema>;
