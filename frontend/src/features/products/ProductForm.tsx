import { zodResolver } from '@hookform/resolvers/zod';
import { useEffect } from 'react';
import { useForm } from 'react-hook-form';

import { CatalogCascade } from '../catalog/CatalogComboboxes';
import {
  productFormSchema,
  type Product,
  type ProductFormInput,
  type ProductFormValues,
} from './schemas';

interface ProductFormProps {
  id: string;
  product?: Product | null;
  brands?: string[];
  categories?: string[];
  onSubmit: (values: ProductFormValues) => void;
  onCreateBrand: () => void;
  onCreateCategory: (brandId: number) => void;
}

function valuesFromProduct(product?: Product | null): ProductFormValues {
  return {
    name: product?.name ?? '',
    article: product?.article ?? '',
    brand: product?.brand ?? '',
    category: product?.category ?? '',
    brand_id: product?.brand_id ?? null,
    category_id: product?.category_id ?? null,
    cell: product?.cell ?? '',
    stock: product?.stock ?? 0,
    stock_reason: '',
  };
}

export function ProductForm({
  id,
  product,
  onSubmit,
  onCreateBrand,
  onCreateCategory,
}: ProductFormProps) {
  const {
    register,
    handleSubmit,
    reset,
    setValue,
    watch,
    formState: { errors },
  } = useForm<ProductFormInput, unknown, ProductFormValues>({
    resolver: zodResolver(productFormSchema),
    defaultValues: valuesFromProduct(product),
  });
  const currentBrandId = watch('brand_id');
  const currentCategoryId = watch('category_id');

  useEffect(() => {
    reset(valuesFromProduct(product));
  }, [product, reset]);

  return (
    <form className="erp-form" id={id} onSubmit={handleSubmit(onSubmit)}>
      <label className="form-field span-2">
        <span>Название *</span>
        <input {...register('name')} placeholder="Например, Casio G-Shock GA-2100" />
        {errors.name ? <small>{errors.name.message}</small> : null}
      </label>
      <label className="form-field">
        <span>Артикул</span>
        <input {...register('article')} placeholder="ART-001" />
      </label>
      <label className="form-field">
        <span>Ячейка</span>
        <input {...register('cell')} placeholder="A-01-02" />
      </label>
      <CatalogCascade
        showProduct={false}
        brandId={currentBrandId}
        categoryId={currentCategoryId}
        initialBrand={
          product?.brand_id
            ? {
                id: product.brand_id,
                name: product.brand,
                active: true,
                product_count: 1,
              }
            : null
        }
        initialCategory={
          product?.category_id && product.brand_id
            ? {
                id: product.category_id,
                brand_id: product.brand_id,
                name: product.category,
                brand_name: product.brand,
                active: true,
                product_count: 1,
              }
            : null
        }
        onBrandChange={(brandId, brand) => {
          setValue('brand_id', brandId, { shouldValidate: true });
          setValue('brand', brand?.name ?? '');
          setValue('category_id', null);
          setValue('category', '');
        }}
        onCategoryChange={(categoryId, category) => {
          setValue('category_id', categoryId, { shouldValidate: true });
          setValue('category', category?.name ?? '');
        }}
        onCreateBrand={onCreateBrand}
        onCreateCategory={onCreateCategory}
      />
      <label className="form-field">
        <span>Остаток</span>
        <input {...register('stock')} type="number" min="0" step="1" />
        {errors.stock ? <small>{errors.stock.message}</small> : null}
      </label>
      <label className="form-field">
        <span>Причина изменения</span>
        <input {...register('stock_reason')} placeholder="Инвентаризация" />
      </label>
    </form>
  );
}
