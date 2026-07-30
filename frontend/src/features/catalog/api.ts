import { z } from 'zod';

import { apiRequest, jsonRequestInit } from '../../api/client';
import { catalogBrandSchema, catalogCategorySchema, catalogProductSchema } from './schemas';

export type CatalogOptionKind = 'brand' | 'category' | 'product';

interface CatalogOptionFilters {
  query?: string;
  brandId?: number | null;
  categoryId?: number | null;
  inStock?: boolean;
  limit?: number;
}

export async function fetchCatalogOptions(
  kind: 'brand',
  filters?: CatalogOptionFilters,
): Promise<z.infer<typeof catalogBrandSchema>[]>;
export async function fetchCatalogOptions(
  kind: 'category',
  filters?: CatalogOptionFilters,
): Promise<z.infer<typeof catalogCategorySchema>[]>;
export async function fetchCatalogOptions(
  kind: 'product',
  filters?: CatalogOptionFilters,
): Promise<z.infer<typeof catalogProductSchema>[]>;
export async function fetchCatalogOptions(
  kind: CatalogOptionKind,
  filters: CatalogOptionFilters = {},
): Promise<
  | z.infer<typeof catalogBrandSchema>[]
  | z.infer<typeof catalogCategorySchema>[]
  | z.infer<typeof catalogProductSchema>[]
> {
  const params = new URLSearchParams({
    type: kind,
    limit: String(filters.limit ?? 50),
  });
  if (filters.query?.trim()) params.set('q', filters.query.trim());
  if (filters.brandId) params.set('brand_id', String(filters.brandId));
  if (filters.categoryId) params.set('category_id', String(filters.categoryId));
  if (filters.inStock) params.set('in_stock', '1');
  if (kind === 'brand') {
    return (await apiRequest(`/catalog/options?${params}`, z.array(catalogBrandSchema))).data;
  }
  if (kind === 'category') {
    return (await apiRequest(`/catalog/options?${params}`, z.array(catalogCategorySchema))).data;
  }
  return (await apiRequest(`/catalog/options?${params}`, z.array(catalogProductSchema))).data;
}

export async function createCatalogBrand(name: string) {
  return (await apiRequest('/brands', catalogBrandSchema, jsonRequestInit('POST', { name }))).data;
}

export async function createCatalogCategory(brandId: number, name: string) {
  return (
    await apiRequest(
      '/categories',
      catalogCategorySchema,
      jsonRequestInit('POST', { brand_id: brandId, name }),
    )
  ).data;
}

export async function createCatalogProduct(values: {
  name: string;
  article: string;
  brand_id: number;
  category_id: number;
}) {
  return (
    await apiRequest(
      '/products',
      z.object({ id: z.number().int().positive() }).passthrough(),
      jsonRequestInit('POST', {
        ...values,
        brand: '',
        category: '',
        cell: '',
        stock: 0,
      }),
    )
  ).data;
}
