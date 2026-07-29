import { zodResolver } from '@hookform/resolvers/zod';
import { useFieldArray, useForm } from 'react-hook-form';

import { ImageUploader } from '../../components/ImageUploader';
import { BrandSelect, CategorySelect, ProductSelect } from '../../components/Controls';
import {
  receiptFormSchema,
  type Receipt,
  type ReceiptCatalogProduct,
  type ReceiptFormInput,
  type ReceiptFormValues,
} from './schemas';

interface ReceiptFormProps {
  id: string;
  receipt?: Receipt | null;
  products: ReceiptCatalogProduct[];
  onSubmit: (values: ReceiptFormValues) => void;
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
          quantity: position.quantity,
          purchase_price: position.purchase_price,
        }))
      : [{ brand: '', category: '', product_id: '', quantity: 1, purchase_price: 0 }],
    product_image: null,
  };
}

export function ReceiptForm({ id, receipt, products, onSubmit }: ReceiptFormProps) {
  const {
    control,
    register,
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
    (sum, position) =>
      sum + Number(position.quantity || 0) * Number(position.purchase_price || 0),
    0,
  );

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
          const selectedBrand = watchedPositions[index]?.brand ?? '';
          const selectedCategory = watchedPositions[index]?.category ?? '';
          const brands = [...new Set(products.map((product) => product.brand).filter(Boolean))];
          const categories = [
            ...new Set(
              products
                .filter((product) => product.brand === selectedBrand)
                .map((product) => product.category)
                .filter(Boolean),
            ),
          ];
          const visibleProducts = products.filter(
            (product) =>
              product.brand === selectedBrand && product.category === selectedCategory,
          );
          const selected = products.find(
            (product) => product.id === watchedPositions[index]?.product_id,
          );
          const brandField = register(`positions.${index}.brand`);
          const categoryField = register(`positions.${index}.category`);
          return (
            <fieldset className="receipt-position" key={field.id}>
              <legend>Позиция {index + 1}</legend>
              <BrandSelect
                label="Бренд *"
                placeholder="Выберите бренд"
                options={brands.map((brand) => ({ value: brand, label: brand }))}
                {...brandField}
                onChange={(event) => {
                  brandField.onChange(event);
                  setValue(`positions.${index}.category`, '');
                  setValue(`positions.${index}.product_id`, '');
                }}
              />
              <CategorySelect
                label="Категория *"
                placeholder="Выберите категорию"
                options={categories.map((category) => ({
                  value: category,
                  label: category,
                }))}
                {...categoryField}
                onChange={(event) => {
                  categoryField.onChange(event);
                  setValue(`positions.${index}.product_id`, '');
                }}
                disabled={!selectedBrand}
              />
              <div className="receipt-product-field">
                <ProductSelect
                  label="Товар *"
                  placeholder="Выберите товар"
                  options={visibleProducts.map((product) => ({
                    value: product.id,
                    label: `${product.name} · остаток ${product.stock_display}`,
                  }))}
                  {...register(`positions.${index}.product_id`)}
                  disabled={!selectedCategory}
                />
                {errors.positions?.[index]?.product_id ? (
                  <small>{errors.positions[index]?.product_id?.message}</small>
                ) : null}
                {selected ? (
                  <em>
                    {[selected.article, selected.category, selected.cell]
                      .filter(Boolean)
                      .join(' · ')}
                  </em>
                ) : null}
              </div>
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
                  ×
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
