import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useMemo, useState } from 'react';

import { SearchableSelect } from '../../components/Controls';
import { useDebouncedValue } from '../../hooks/useDebouncedValue';
import {
  createCatalogBrand,
  createCatalogCategory,
  createCatalogProduct,
  fetchCatalogOptions,
} from './api';
import type { CatalogBrand, CatalogCategory, CatalogProduct } from './schemas';

interface EntityComboboxProps<T> {
  value: string;
  options: T[];
  optionValue: (option: T) => string;
  optionLabel: (option: T) => string;
  optionDetails?: (option: T) => {
    inputLabel?: string;
    description?: string;
    meta?: string;
    searchText?: string;
  };
  label: string;
  placeholder: string;
  disabled?: boolean;
  required?: boolean;
  loading?: boolean;
  error?: string;
  onChange: (value: string, option?: T) => void;
  onQueryChange: (query: string) => void;
  createAction?: {
    onCreate: (name: string) => void | Promise<void>;
    loading?: boolean;
  };
}

function EntityCombobox<T>({
  value,
  options,
  optionValue,
  optionLabel,
  optionDetails,
  onChange,
  ...props
}: EntityComboboxProps<T>) {
  return (
    <SearchableSelect
      {...props}
      value={value}
      options={options.map((option) => ({
        value: optionValue(option),
        label: optionLabel(option),
        ...optionDetails?.(option),
      }))}
      onChange={(nextValue) =>
        onChange(
          nextValue,
          options.find((option) => optionValue(option) === nextValue),
        )
      }
      emptyLabel="Ничего не найдено"
    />
  );
}

export const BrandCombobox = EntityCombobox<CatalogBrand>;
export const CategoryCombobox = EntityCombobox<CatalogCategory>;
export const ProductCombobox = EntityCombobox<CatalogProduct>;

interface CatalogCascadeProps {
  brandId: number | null;
  categoryId: number | null;
  productId?: string;
  onBrandChange: (brandId: number | null, brand?: CatalogBrand) => void;
  onCategoryChange: (categoryId: number | null, category?: CatalogCategory) => void;
  onProductChange?: (productId: string, product?: CatalogProduct) => void;
  showProduct?: boolean;
  inStock?: boolean;
  disabled?: boolean;
  productDisabled?: boolean;
  required?: boolean;
  errors?: {
    brand?: string;
    category?: string;
    product?: string;
  };
  initialBrand?: CatalogBrand | null;
  initialCategory?: CatalogCategory | null;
  initialProduct?: CatalogProduct | null;
  allowCreate?: boolean;
  onCatalogCreated?: (message: string) => void;
}

export function CatalogCascade({
  brandId,
  categoryId,
  productId = '',
  onBrandChange,
  onCategoryChange,
  onProductChange,
  showProduct = true,
  inStock = false,
  disabled = false,
  productDisabled = false,
  required = true,
  errors,
  initialBrand,
  initialCategory,
  initialProduct,
  allowCreate = false,
  onCatalogCreated,
}: CatalogCascadeProps) {
  const queryClient = useQueryClient();
  const [brandQuery, setBrandQuery] = useState('');
  const [categoryQuery, setCategoryQuery] = useState('');
  const [productQuery, setProductQuery] = useState('');
  const [creatingKind, setCreatingKind] = useState<'brand' | 'category' | 'product' | null>(null);
  const [creationError, setCreationError] = useState<
    Partial<Record<'brand' | 'category' | 'product', string>>
  >({});
  const [createdBrand, setCreatedBrand] = useState<CatalogBrand | null>(null);
  const [createdCategory, setCreatedCategory] = useState<CatalogCategory | null>(null);
  const [createdProduct, setCreatedProduct] = useState<CatalogProduct | null>(null);
  const [categoryLinkError, setCategoryLinkError] = useState('');
  const debouncedBrandQuery = useDebouncedValue(brandQuery, 250);
  const debouncedCategoryQuery = useDebouncedValue(categoryQuery, 250);
  const debouncedProductQuery = useDebouncedValue(productQuery, 250);

  useEffect(() => {
    setCategoryQuery('');
    setProductQuery('');
    setCreatedCategory(null);
    setCreatedProduct(null);
    setCategoryLinkError('');
  }, [brandId]);

  useEffect(() => {
    setProductQuery('');
    setCreatedProduct(null);
  }, [categoryId]);

  const brandsQuery = useQuery({
    queryKey: ['catalog-options', 'brand', debouncedBrandQuery],
    queryFn: ({ signal }) =>
      fetchCatalogOptions(
        'brand',
        {
          query: debouncedBrandQuery,
        },
        signal,
      ),
    staleTime: 5 * 60_000,
  });
  const categoriesQuery = useQuery({
    queryKey: ['catalog-options', 'category', brandId, debouncedCategoryQuery],
    queryFn: ({ signal }) =>
      fetchCatalogOptions(
        'category',
        {
          brandId,
          query: debouncedCategoryQuery,
        },
        signal,
      ),
    enabled: Boolean(brandId),
    staleTime: 2 * 60_000,
  });
  const globalCategoriesQuery = useQuery({
    queryKey: ['catalog-options', 'category-global', debouncedCategoryQuery],
    queryFn: ({ signal }) =>
      fetchCatalogOptions(
        'category',
        {
          query: debouncedCategoryQuery,
          limit: 100,
        },
        signal,
      ),
    enabled: allowCreate && Boolean(brandId),
    staleTime: 2 * 60_000,
  });
  const productsQuery = useQuery({
    queryKey: ['catalog-options', 'product', brandId, categoryId, debouncedProductQuery, inStock],
    queryFn: ({ signal }) =>
      fetchCatalogOptions(
        'product',
        {
          brandId,
          categoryId,
          query: debouncedProductQuery,
          inStock,
        },
        signal,
      ),
    enabled: showProduct && Boolean(brandId && categoryId),
    staleTime: 30_000,
  });

  const brands = useMemo(() => {
    const items = brandsQuery.data ?? [];
    const pinned = createdBrand?.id === brandId ? createdBrand : initialBrand;
    return pinned && !items.some((item) => item.id === pinned.id) ? [pinned, ...items] : items;
  }, [brandId, brandsQuery.data, createdBrand, initialBrand]);
  const categories = useMemo(() => {
    const items = categoriesQuery.data ?? [];
    const linkedNames = new Set(items.map((item) => item.name.trim().toLocaleLowerCase('ru-RU')));
    const globalTemplates = (globalCategoriesQuery.data ?? []).filter((item) => {
      const key = item.name.trim().toLocaleLowerCase('ru-RU');
      if (linkedNames.has(key)) return false;
      linkedNames.add(key);
      return true;
    });
    const available = [...items, ...globalTemplates];
    const pinned = createdCategory?.id === categoryId ? createdCategory : initialCategory;
    return pinned && pinned.brand_id === brandId && !available.some((item) => item.id === pinned.id)
      ? [pinned, ...available]
      : available;
  }, [
    brandId,
    categoriesQuery.data,
    categoryId,
    createdCategory,
    globalCategoriesQuery.data,
    initialCategory,
  ]);
  const products = useMemo(() => {
    const items = productsQuery.data ?? [];
    const pinned = createdProduct?.id === productId ? createdProduct : initialProduct;
    return pinned &&
      pinned.brand_id === brandId &&
      pinned.category_id === categoryId &&
      !items.some((item) => item.id === pinned.id)
      ? [pinned, ...items]
      : items;
  }, [brandId, categoryId, createdProduct, initialProduct, productId, productsQuery.data]);

  const createCatalogValue = async (kind: 'brand' | 'category' | 'product', rawName: string) => {
    const name = rawName.replace(/\s+/g, ' ').trim();
    if (!name || creatingKind) return;
    setCreatingKind(kind);
    setCreationError((current) => ({ ...current, [kind]: '' }));
    try {
      if (kind === 'brand') {
        const created = await createCatalogBrand(name);
        setCreatedBrand(created);
        onBrandChange(created.id, created);
        onCatalogCreated?.('Бренд создан');
      } else if (kind === 'category') {
        if (!brandId) throw new Error('Сначала выберите бренд');
        const created = await createCatalogCategory(brandId, name);
        setCreatedCategory(created);
        onCategoryChange(created.id, created);
        onCatalogCreated?.('Категория создана');
      } else {
        if (!brandId || !categoryId) throw new Error('Сначала выберите бренд и категорию');
        const created = await createCatalogProduct({
          name,
          article: '',
          brand_id: brandId,
          category_id: categoryId,
          product_image: null,
        });
        setCreatedProduct(created);
        onProductChange?.(created.id, created);
        onCatalogCreated?.('Товар создан и выбран');
      }
      void Promise.all([
        queryClient.invalidateQueries({ queryKey: ['catalog-options'] }),
        queryClient.invalidateQueries({ queryKey: ['products'] }),
      ]);
    } catch (error) {
      setCreationError((current) => ({
        ...current,
        [kind]: error instanceof Error ? error.message : 'Не удалось создать значение',
      }));
      throw error;
    } finally {
      setCreatingKind(null);
    }
  };

  return (
    <>
      <div
        className={`catalog-cascade${showProduct ? '' : ' is-taxonomy-only'}`}
        data-testid="catalog-cascade"
      >
        <BrandCombobox
          label="Бренд"
          required={required}
          placeholder="Найдите бренд"
          value={brandId ? String(brandId) : ''}
          options={brands}
          optionValue={(option) => String(option.id)}
          optionLabel={(option) => option.name}
          onQueryChange={setBrandQuery}
          loading={brandsQuery.isFetching}
          disabled={disabled}
          error={creationError.brand || errors?.brand}
          onChange={(value, option) => {
            onBrandChange(value ? Number(value) : null, option);
          }}
          createAction={
            allowCreate
              ? {
                  onCreate: (name) => createCatalogValue('brand', name),
                  loading: creatingKind === 'brand',
                }
              : undefined
          }
        />
        <CategoryCombobox
          label="Категория"
          required={required}
          placeholder={brandId ? 'Найдите категорию' : 'Сначала выберите бренд'}
          value={categoryId ? String(categoryId) : ''}
          options={categories}
          optionValue={(option) => String(option.id)}
          optionLabel={(option) => option.name}
          onQueryChange={setCategoryQuery}
          loading={categoriesQuery.isFetching || globalCategoriesQuery.isFetching}
          disabled={disabled || !brandId}
          error={creationError.category || categoryLinkError || errors?.category}
          onChange={(value, option) => {
            if (!value || !option || option.brand_id === brandId) {
              setCategoryLinkError('');
              onCategoryChange(value ? Number(value) : null, option);
              return;
            }
            if (!brandId) return;
            void createCatalogCategory(brandId, option.name)
              .then((created) => {
                setCategoryLinkError('');
                setCreatedCategory(created);
                onCategoryChange(created.id, created);
                onCatalogCreated?.('Категория связана с новым брендом');
              })
              .catch(async () => {
                const refreshed = await categoriesQuery.refetch();
                const linked = refreshed.data?.find(
                  (item) =>
                    item.name.trim().toLocaleLowerCase('ru-RU') ===
                    option.name.trim().toLocaleLowerCase('ru-RU'),
                );
                if (linked) {
                  setCategoryLinkError('');
                  setCreatedCategory(linked);
                  onCategoryChange(linked.id, linked);
                } else {
                  setCategoryLinkError('Не удалось связать категорию с брендом');
                }
              });
          }}
          createAction={
            allowCreate && brandId
              ? {
                  onCreate: (name) => createCatalogValue('category', name),
                  loading: creatingKind === 'category',
                }
              : undefined
          }
        />
        {showProduct ? (
          <ProductCombobox
            label="Товар"
            required={required}
            placeholder={categoryId ? 'Найдите товар' : 'Сначала выберите категорию'}
            value={productId}
            options={products}
            optionValue={(option) => option.id}
            optionLabel={(option) => option.name}
            optionDetails={(option) => ({
              inputLabel: option.name,
              description: `Артикул: ${option.article || '—'}`,
              meta: `Остаток: ${option.stock_display}`,
              searchText: [option.name, option.article, option.barcode].filter(Boolean).join(' '),
            })}
            onQueryChange={setProductQuery}
            loading={productsQuery.isFetching}
            disabled={disabled || productDisabled || !categoryId}
            error={creationError.product || errors?.product}
            onChange={(value, option) => onProductChange?.(value, option)}
            createAction={
              allowCreate && brandId && categoryId
                ? {
                    onCreate: (name) => createCatalogValue('product', name),
                    loading: creatingKind === 'product',
                  }
                : undefined
            }
          />
        ) : null}
      </div>
    </>
  );
}
