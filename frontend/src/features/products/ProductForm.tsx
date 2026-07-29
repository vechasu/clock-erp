import { zodResolver } from '@hookform/resolvers/zod';
import { useEffect } from 'react';
import { Controller, useForm } from 'react-hook-form';

import { SearchableSelect } from '../../components/Controls';
import {
  productFormSchema,
  type Product,
  type ProductFormInput,
  type ProductFormValues,
} from './schemas';

interface ProductFormProps {
  id: string;
  product?: Product | null;
  onSubmit: (values: ProductFormValues) => void;
  onCreateBrand: () => void;
  onCreateCategory: (brand: string) => void;
  brands?: string[];
  categories?: string[];
}

function valuesFromProduct(product?: Product | null): ProductFormValues {
  return {
    name: product?.name ?? '',
    article: product?.article ?? '',
    brand: product?.brand ?? '',
    category: product?.category ?? '',
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
  brands = [],
  categories = [],
}: ProductFormProps) {
  const {
    register,
    control,
    handleSubmit,
    reset,
    watch,
    formState: { errors },
  } = useForm<ProductFormInput, unknown, ProductFormValues>({
    resolver: zodResolver(productFormSchema),
    defaultValues: valuesFromProduct(product),
  });
  const currentBrand = watch('brand');

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
      <div className="form-field">
        <span className="field-label-with-action">
          Бренд
          <button type="button" onClick={onCreateBrand}>
            Новый бренд
          </button>
        </span>
        <Controller
          control={control}
          name="brand"
          render={({ field }) => (
            <SearchableSelect
              label="Выберите или найдите бренд"
              placeholder="Например, Casio"
              options={[...new Set([field.value, ...brands].filter(Boolean))].map((brand) => ({
                value: brand,
                label: brand,
              }))}
              value={field.value}
              onChange={field.onChange}
            />
          )}
        />
      </div>
      <div className="form-field">
        <span className="field-label-with-action">
          Категория
          <button type="button" onClick={() => onCreateCategory(currentBrand)}>
            Новая категория
          </button>
        </span>
        <Controller
          control={control}
          name="category"
          render={({ field }) => (
            <SearchableSelect
              label="Выберите или найдите категорию"
              placeholder="Например, Спортивные часы"
              options={[...new Set([field.value, ...categories].filter(Boolean))].map(
                (category) => ({ value: category, label: category }),
              )}
              value={field.value}
              onChange={field.onChange}
            />
          )}
        />
      </div>
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
