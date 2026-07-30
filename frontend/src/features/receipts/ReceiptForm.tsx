import { zodResolver } from '@hookform/resolvers/zod';
import { useEffect } from 'react';
import { useFieldArray, useForm } from 'react-hook-form';

import { ImageUploader } from '../../components/ImageUploader';
import { Icon } from '../../components/Icons';
import { CatalogCascade } from '../catalog/CatalogComboboxes';
import {
  receiptFormSchema,
  type Receipt,
  type ReceiptFormInput,
  type ReceiptFormValues,
} from './schemas';

interface ReceiptFormProps {
  id: string;
  receipt?: Receipt | null;
  onSubmit: (values: ReceiptFormValues) => void;
  onCatalogCreated?: (message: string) => void;
}

function defaults(receipt?: Receipt | null): ReceiptFormInput {
  return {
    receipt_date: receipt?.receipt_date ?? new Date().toISOString().slice(0, 10),
    note: receipt?.note ?? '',
    positions: receipt?.positions.length
      ? receipt.positions.map((position) => ({
          product_id: position.product_id,
          brand: position.brand,
          category: position.category,
          brand_id: (position.brand_id ?? null) as unknown as number,
          category_id: (position.category_id ?? null) as unknown as number,
          quantity: position.quantity,
          purchase_price: position.purchase_price,
        }))
      : [
          {
            brand: '',
            category: '',
            brand_id: null as unknown as number,
            category_id: null as unknown as number,
            product_id: '',
            quantity: 1,
            purchase_price: 0,
          },
        ],
    product_image: null,
  };
}

export function ReceiptForm({ id, receipt, onSubmit, onCatalogCreated }: ReceiptFormProps) {
  const {
    control,
    register,
    reset,
    setValue,
    watch,
    handleSubmit,
    formState: { errors },
  } = useForm<ReceiptFormInput, unknown, ReceiptFormValues>({
    resolver: zodResolver(receiptFormSchema),
    defaultValues: defaults(receipt),
  });
  const { fields, append, remove } = useFieldArray({ control, name: 'positions' });
  const watchedPositions = watch('positions');
  const total = watchedPositions.reduce(
    (sum, position) => sum + Number(position.quantity || 0) * Number(position.purchase_price || 0),
    0,
  );

  useEffect(() => {
    reset(defaults(receipt));
  }, [receipt, reset]);

  return (
    <form className="receipt-form" id={id} onSubmit={handleSubmit(onSubmit)}>
      <div className="erp-form">
        <label className="form-field">
          <span>Дата прихода *</span>
          <input type="date" {...register('receipt_date')} />
          {errors.receipt_date ? <small>{errors.receipt_date.message}</small> : null}
        </label>
        <label className="form-field">
          <span>Комментарий</span>
          <input {...register('note')} placeholder="Необязательное примечание" />
        </label>
      </div>
      <div className="receipt-positions-header">
        <div>
          <strong>Позиции прихода</strong>
          <small>Товар, количество и закупочная цена</small>
        </div>
        {!receipt ? (
          <div className="receipt-position-actions">
            <a className="button secondary" href="/app/products?open_add=1">
              Новый товар / справочник
            </a>
            <button
              className="button secondary"
              type="button"
              onClick={() =>
                append({
                  brand: '',
                  category: '',
                  brand_id: null as unknown as number,
                  category_id: null as unknown as number,
                  product_id: '',
                  quantity: 1,
                  purchase_price: 0,
                })
              }
            >
              + Добавить позицию
            </button>
          </div>
        ) : null}
      </div>
      <div className="receipt-positions">
        {fields.map((field, index) => {
          const selectedBrandId = watchedPositions[index]?.brand_id ?? null;
          const selectedCategoryId = watchedPositions[index]?.category_id ?? null;
          const selectedProductId = watchedPositions[index]?.product_id ?? '';
          const existingPosition = receipt?.positions[index];
          return (
            <fieldset className="receipt-position" key={field.id}>
              <legend>Позиция {index + 1}</legend>
              <CatalogCascade
                allowCreate
                brandId={selectedBrandId}
                categoryId={selectedCategoryId}
                productId={selectedProductId}
                initialBrand={
                  existingPosition?.brand_id
                    ? {
                        id: existingPosition.brand_id,
                        name: existingPosition.brand,
                        active: true,
                        product_count: 1,
                      }
                    : null
                }
                initialCategory={
                  existingPosition?.category_id && existingPosition.brand_id
                    ? {
                        id: existingPosition.category_id,
                        brand_id: existingPosition.brand_id,
                        name: existingPosition.category,
                        brand_name: existingPosition.brand,
                        active: true,
                        product_count: 1,
                      }
                    : null
                }
                initialProduct={
                  existingPosition?.brand_id && existingPosition.category_id
                    ? {
                        id: existingPosition.product_id,
                        product_id: existingPosition.product_id,
                        name: existingPosition.product_name,
                        article: existingPosition.article,
                        barcode: existingPosition.code,
                        brand_id: existingPosition.brand_id,
                        category_id: existingPosition.category_id,
                        brand: existingPosition.brand,
                        category: existingPosition.category,
                        cell: existingPosition.cell,
                        stock: existingPosition.stock_after,
                        stock_display: String(existingPosition.stock_after),
                        active: true,
                      }
                    : null
                }
                onBrandChange={(brandId, brand) => {
                  setValue(`positions.${index}.brand_id`, brandId as number, {
                    shouldValidate: true,
                  });
                  setValue(`positions.${index}.brand`, brand?.name ?? '');
                  setValue(`positions.${index}.category_id`, null as unknown as number);
                  setValue(`positions.${index}.category`, '');
                  setValue(`positions.${index}.product_id`, '');
                }}
                onCategoryChange={(categoryId, category) => {
                  setValue(`positions.${index}.category_id`, categoryId as number, {
                    shouldValidate: true,
                  });
                  setValue(`positions.${index}.category`, category?.name ?? '');
                  setValue(`positions.${index}.product_id`, '');
                }}
                onProductChange={(productId) =>
                  setValue(`positions.${index}.product_id`, productId, {
                    shouldValidate: true,
                  })
                }
                onCatalogCreated={onCatalogCreated}
                errors={{
                  brand: errors.positions?.[index]?.brand_id?.message,
                  category: errors.positions?.[index]?.category_id?.message,
                  product: errors.positions?.[index]?.product_id?.message,
                }}
              />
              <label className="form-field">
                <span>Количество *</span>
                <input
                  type="number"
                  min="0.01"
                  step="0.01"
                  {...register(`positions.${index}.quantity`)}
                />
                {errors.positions?.[index]?.quantity ? (
                  <small>{errors.positions[index]?.quantity?.message}</small>
                ) : null}
              </label>
              <label className="form-field">
                <span>Цена закупки</span>
                <input
                  type="number"
                  min="0"
                  step="0.01"
                  {...register(`positions.${index}.purchase_price`)}
                />
                {errors.positions?.[index]?.purchase_price ? (
                  <small>{errors.positions[index]?.purchase_price?.message}</small>
                ) : null}
              </label>
              {!receipt && fields.length > 1 ? (
                <button
                  className="remove-position"
                  type="button"
                  onClick={() => remove(index)}
                  aria-label={`Удалить позицию ${index + 1}`}
                >
                  <Icon name="close" />
                </button>
              ) : null}
            </fieldset>
          );
        })}
      </div>
      {!receipt && fields.length === 1 ? (
        <ImageUploader
          onChange={(image) => setValue('product_image', image, { shouldDirty: true })}
        />
      ) : null}
      <div className="receipt-form-total">
        <span>Итого</span>
        <strong>{total.toLocaleString('ru-RU', { maximumFractionDigits: 2 })} ₽</strong>
      </div>
    </form>
  );
}
