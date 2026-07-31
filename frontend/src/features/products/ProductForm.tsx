import { zodResolver } from '@hookform/resolvers/zod';
import { useEffect, useState, type KeyboardEvent } from 'react';
import { useForm } from 'react-hook-form';

import { FileUpload } from '../../components/FileUpload';
import { CatalogCascade } from '../catalog/CatalogComboboxes';
import {
  productFormSchema,
  type Product,
  type ProductFormInput,
  type ProductFormValues,
} from './schemas';

const PRODUCT_IMAGE_MAX_BYTES = 3 * 1024 * 1024;
const PRODUCT_IMAGE_ACCEPT = [
  '.jpg',
  '.jpeg',
  '.png',
  '.webp',
  'image/jpeg',
  'image/jpg',
  'image/png',
  'image/webp',
];

interface ProductFormProps {
  id: string;
  product?: Product | null;
  pending?: boolean;
  onSubmit: (values: ProductFormValues, image: File | null) => void;
  onCatalogCreated?: (message: string) => void;
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

function preventNonIntegerKeys(event: KeyboardEvent<HTMLInputElement>) {
  if (['-', '+', '.', ',', 'e', 'E'].includes(event.key)) event.preventDefault();
}

export function ProductForm({
  id,
  product,
  pending = false,
  onSubmit,
  onCatalogCreated,
}: ProductFormProps) {
  const [image, setImage] = useState<File | null>(null);
  const [imageError, setImageError] = useState('');
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
  const currentStock = Math.max(0, Math.floor(Number(watch('stock')) || 0));
  const stockRegistration = register('stock');

  useEffect(() => {
    reset(valuesFromProduct(product));
    setImage(null);
    setImageError('');
  }, [product, reset]);

  const catalogFields = (
    <CatalogCascade
      showProduct={false}
      allowCreate
      disabled={pending}
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
      onCatalogCreated={onCatalogCreated}
    />
  );

  const stockField = (
    <label className={`form-field stock-stepper-field${errors.stock ? ' has-error' : ''}`}>
      <span>{product ? 'Остаток' : 'Начальный остаток'}</span>
      <span className="stock-stepper">
        <button
          type="button"
          aria-label="Уменьшить начальный остаток"
          disabled={pending || currentStock === 0}
          onClick={() =>
            setValue('stock', Math.max(0, currentStock - 1), {
              shouldDirty: true,
              shouldValidate: true,
            })
          }
        >
          −
        </button>
        <input
          {...stockRegistration}
          type="number"
          min="0"
          step="1"
          inputMode="numeric"
          disabled={pending}
          aria-label={product ? 'Остаток' : 'Начальный остаток'}
          onKeyDown={preventNonIntegerKeys}
          onBlur={(event) => {
            stockRegistration.onBlur(event);
            const parsed = Number(event.target.value);
            setValue('stock', Number.isFinite(parsed) ? Math.max(0, Math.floor(parsed)) : 0, {
              shouldDirty: true,
              shouldValidate: true,
            });
          }}
        />
        <button
          type="button"
          aria-label="Увеличить начальный остаток"
          disabled={pending}
          onClick={() =>
            setValue('stock', currentStock + 1, {
              shouldDirty: true,
              shouldValidate: true,
            })
          }
        >
          +
        </button>
      </span>
      {errors.stock ? <small>{errors.stock.message}</small> : null}
    </label>
  );

  return (
    <form
      className={product ? 'erp-form product-edit-form' : 'product-create-card'}
      id={id}
      onSubmit={handleSubmit((values) => onSubmit(values, image))}
    >
      {product ? (
        <>
          <label className="form-field span-2">
            <span>Название *</span>
            <input
              {...register('name')}
              disabled={pending}
              placeholder="Например, Casio G-Shock GA-2100"
            />
            {errors.name ? <small>{errors.name.message}</small> : null}
          </label>
          <label className="form-field">
            <span>Артикул</span>
            <input
              {...register('article')}
              disabled={pending}
              placeholder="Например, GA-2100-1A1"
            />
          </label>
          <label className="form-field">
            <span>Ячейка</span>
            <input {...register('cell')} disabled={pending} placeholder="Например, A-01-02" />
          </label>
          {catalogFields}
          {stockField}
          <label className="form-field">
            <span>Причина изменения</span>
            <input
              {...register('stock_reason')}
              disabled={pending}
              placeholder="Например, инвентаризация"
            />
          </label>
        </>
      ) : (
        <>
          <div className="product-create-primary">
            <FileUpload
              label="Фото"
              accept={PRODUCT_IMAGE_ACCEPT}
              maxSize={PRODUCT_IMAGE_MAX_BYTES}
              value={image}
              onChange={setImage}
              onErrorChange={setImageError}
              disabled={pending}
              compactImage
            />
            <label className={`form-field product-create-name${errors.name ? ' has-error' : ''}`}>
              <span>Название товара *</span>
              <input
                {...register('name')}
                disabled={pending}
                placeholder="Например, Casio G-Shock GA-2100"
              />
              {errors.name ? <small>{errors.name.message}</small> : null}
            </label>
            <label className="form-field product-create-article">
              <span>Артикул</span>
              <input
                {...register('article')}
                disabled={pending}
                placeholder="Например, GA-2100-1A1"
              />
            </label>
            <div className="product-create-taxonomy">{catalogFields}</div>
          </div>
          <div className="product-create-secondary">
            {stockField}
            <label className="form-field product-create-cell">
              <span>Ячейка</span>
              <input {...register('cell')} disabled={pending} placeholder="Например, A-01-02" />
            </label>
            <button
              className="button primary product-create-submit"
              type="submit"
              disabled={pending || Boolean(imageError)}
            >
              {pending ? 'Добавляем…' : 'Добавить'}
            </button>
          </div>
        </>
      )}
    </form>
  );
}
