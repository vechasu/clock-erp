import { useQuery } from '@tanstack/react-query';
import { useEffect, useMemo, useState } from 'react';

import { SearchableSelect } from '../../components/Controls';
import { useDebouncedValue } from '../../hooks/useDebouncedValue';
import { fetchCatalogOptions } from './api';
import type { CatalogBrand, CatalogCategory, CatalogProduct } from './schemas';

interface EntityComboboxProps<T> {
  value: string;
  options: T[];
  optionValue: (option: T) => string;
  optionLabel: (option: T) => string;
  label: string;
  placeholder: string;
  disabled?: boolean;
  required?: boolean;
  loading?: boolean;
  error?: string;
  onChange: (value: string, option?: T) => void;
  onQueryChange: (query: string) => void;
  createAction?: {
    label: string;
    onClick: () => void;
  };
}

function EntityCombobox<T>({
  value,
  options,
  optionValue,
  optionLabel,
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
      }))}
      onChange={(nextValue) =>
        onChange(
          nextValue,
          options.find((option) => optionValue(option) === nextValue),
        )
      }
      emptyLabel="Связанных значений не найдено"
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
  onCreateBrand?: () => void;
  onCreateCategory?: (brandId: number) => void;
  onCreateProduct?: (brandId: number, categoryId: number) => void;
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
  onCreateBrand,
  onCreateCategory,
  onCreateProduct,
}: CatalogCascadeProps) {
  const [brandQuery, setBrandQuery] = useState('');
  const [categoryQuery, setCategoryQuery] = useState('');
  const [productQuery, setProductQuery] = useState('');
  const debouncedBrandQuery = useDebouncedValue(brandQuery, 180);
  const debouncedCategoryQuery = useDebouncedValue(categoryQuery, 180);
  const debouncedProductQuery = useDebouncedValue(productQuery, 180);

  useEffect(() => {
    setCategoryQuery('');
    setProductQuery('');
  }, [brandId]);

  useEffect(() => setProductQuery(''), [categoryId]);

  const brandsQuery = useQuery({
    queryKey: ['catalog-options', 'brand', debouncedBrandQuery],
    queryFn: () =>
      fetchCatalogOptions('brand', {
        query: debouncedBrandQuery,
      }),
    staleTime: 60_000,
  });
  const categoriesQuery = useQuery({
    queryKey: ['catalog-options', 'category', brandId, debouncedCategoryQuery],
    queryFn: () =>
      fetchCatalogOptions('category', {
        brandId,
        query: debouncedCategoryQuery,
      }),
    enabled: Boolean(brandId),
    staleTime: 60_000,
  });
  const productsQuery = useQuery({
    queryKey: ['catalog-options', 'product', brandId, categoryId, debouncedProductQuery, inStock],
    queryFn: () =>
      fetchCatalogOptions('product', {
        brandId,
        categoryId,
        query: debouncedProductQuery,
        inStock,
      }),
    enabled: showProduct && Boolean(brandId && categoryId),
    staleTime: 30_000,
  });

  const brands = useMemo(() => {
    const items = brandsQuery.data ?? [];
    return initialBrand && !items.some((item) => item.id === initialBrand.id)
      ? [initialBrand, ...items]
      : items;
  }, [brandsQuery.data, initialBrand]);
  const categories = useMemo(() => {
    const items = categoriesQuery.data ?? [];
    return initialCategory &&
      initialCategory.brand_id === brandId &&
      !items.some((item) => item.id === initialCategory.id)
      ? [initialCategory, ...items]
      : items;
  }, [brandId, categoriesQuery.data, initialCategory]);
  const products = useMemo(() => {
    const items = productsQuery.data ?? [];
    return initialProduct &&
      initialProduct.brand_id === brandId &&
      initialProduct.category_id === categoryId &&
      !items.some((item) => item.id === initialProduct.id)
      ? [initialProduct, ...items]
      : items;
  }, [brandId, categoryId, initialProduct, productsQuery.data]);

  return (
    <>
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
        error={errors?.brand}
        onChange={(value, option) => {
          onBrandChange(value ? Number(value) : null, option);
        }}
        createAction={
          onCreateBrand ? { label: '+ Добавить новый бренд', onClick: onCreateBrand } : undefined
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
        loading={categoriesQuery.isFetching}
        disabled={disabled || !brandId}
        error={errors?.category}
        onChange={(value, option) => {
          onCategoryChange(value ? Number(value) : null, option);
        }}
        createAction={
          onCreateCategory && brandId
            ? {
                label: '+ Добавить новую категорию',
                onClick: () => onCreateCategory(brandId),
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
          optionLabel={(option) =>
            `${option.name} · ${option.article || 'без артикула'} · остаток ${option.stock_display}`
          }
          onQueryChange={setProductQuery}
          loading={productsQuery.isFetching}
          disabled={disabled || productDisabled || !categoryId}
          error={errors?.product}
          onChange={(value, option) => onProductChange?.(value, option)}
          createAction={
            onCreateProduct && brandId && categoryId
              ? {
                  label: '+ Добавить новый товар',
                  onClick: () => onCreateProduct(brandId, categoryId),
                }
              : undefined
          }
        />
      ) : null}
    </>
  );
}
