import { zodResolver } from '@hookform/resolvers/zod';
import { useEffect } from 'react';
import { useForm } from 'react-hook-form';

import {
  productFormSchema,
  type Product,
  type ProductFormInput,
  type ProductFormValues,
} from './schemas';

interface ProductFormProps {
  id: string;
  product?: Product | null;
  pending?: boolean;
  onSubmit: (values: ProductFormValues) => void;
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

export function ProductForm({ id, product, pending, onSubmit }: ProductFormProps) {
  const {
    register,
    handleSubmit,
    reset,
    formState: { errors },
  } = useForm<ProductFormInput, unknown, ProductFormValues>({
    resolver: zodResolver(productFormSchema),
    defaultValues: valuesFromProduct(product),
  });

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
      <label className="form-field">
        <span>Бренд</span>
        <input {...register('brand')} placeholder="Casio" />
      </label>
      <label className="form-field">
        <span>Категория</span>
        <input {...register('category')} placeholder="Часы / Спортивные" />
      </label>
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
