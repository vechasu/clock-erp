import { zodResolver } from '@hookform/resolvers/zod';
import { useEffect } from 'react';
import { Controller, useForm } from 'react-hook-form';

import { SearchableSelect } from '../../components/Controls';
import { CatalogCascade } from '../catalog/CatalogComboboxes';
import {
  saleFormSchema,
  type Sale,
  type SaleFormInput,
  type SaleFormValues,
  type SaleLocations,
} from './schemas';

interface SaleFormProps {
  id: string;
  sale?: Sale | null;
  locations: SaleLocations;
  onSubmit: (values: SaleFormValues) => void;
  onCatalogCreated?: (message: string) => void;
}

const RUSSIAN_REGION_PRIORITIES = [
  "Москва",
  "Санкт-Петербург",
];

const AMAZON_COUNTRY_PRIORITIES = ['США', 'Япония', 'Канада', 'Мексика'];

function localDateTimeValue(value?: string) {
  if (value) return value.replace(' ', 'T').slice(0, 16);
  const now = new Date(Date.now() - new Date().getTimezoneOffset() * 60_000);
  return now.toISOString().slice(0, 16);
}

function normalizeAmazonCountry(value: string) {
  return ['америка', 'usa', 'us', 'united states', 'united states of america'].includes(
    value.trim().toLocaleLowerCase('ru-RU'),
  )
    ? 'США'
    : value;
}

function getOrderedRegions(country: string, locations: SaleLocations) {
  const rawRegions = Object.keys(locations[country] ?? {});
  const uniqueRegions = [...new Set(rawRegions)];
  if (country !== "Россия") {
    return uniqueRegions;
  }

  const prioritySet = new Set(RUSSIAN_REGION_PRIORITIES);
  const head = RUSSIAN_REGION_PRIORITIES.filter(
    (region) => uniqueRegions.includes(region),
  );
  const tail = uniqueRegions
    .filter((region) => !prioritySet.has(region))
    .sort((a, b) => a.localeCompare(b, "ru", { sensitivity: "base" }));

  return [...head, ...tail];
}

function defaults(sale?: Sale | null): SaleFormInput {
  return {
    created_at: localDateTimeValue(sale?.created_at),
    source: sale?.source || 'Tictactoy',
    product_id: sale?.product_id || '',
    product_name: sale?.product_name || '',
    brand_id: sale?.brand_id ?? (null as unknown as number),
    category_id: sale?.category_id ?? (null as unknown as number),
    quantity: sale?.quantity || 1,
    unit_price: sale?.unit_price || 1,
    order_number: sale?.order_number || '',
    order_status: sale?.order_status || 'completed',
    track_number: sale?.track_number || '',
    delivery_method: sale?.delivery_method || '',
    delivery_cost: sale?.delivery_cost || 0,
    country: sale?.source_key === 'amazon' ? normalizeAmazonCountry(sale.country) : sale?.country || '',
    region: sale?.region || '',
    city: sale?.city || '',
    delivery_address: sale?.delivery_address || '',
    recipient: sale?.recipient || '',
    recipient_name: sale?.recipient_name || '',
    payment_method: sale?.payment_method || '',
    commission: sale?.commission || '',
    commission_amount: sale?.commission_amount || 0,
    platform: sale?.platform || '',
    invoice_number: sale?.invoice_number || '',
    sticker_number: sale?.sticker_number || '',
    note: sale?.note || '',
  };
}

export function SaleForm({ id, sale, locations, onSubmit, onCatalogCreated }: SaleFormProps) {
  const {
    control,
    register,
    reset,
    setValue,
    watch,
    handleSubmit,
    formState: { errors },
  } = useForm<SaleFormInput, unknown, SaleFormValues>({
    resolver: zodResolver(saleFormSchema),
    defaultValues: defaults(sale),
  });
  const productId = watch('product_id');
  const brandId = watch('brand_id');
  const categoryId = watch('category_id');
  const country = watch('country');
  const region = watch('region');
  const source = watch('source');
  const sourceKey = source.toLocaleLowerCase('ru-RU');
  const isTictactoy = sourceKey === 'tictactoy';
  const isWildberries = sourceKey === 'wildberries';
  const isAmazon = sourceKey === 'amazon';
  const countryOptions = isAmazon
    ? [
        ...AMAZON_COUNTRY_PRIORITIES,
        ...Object.keys(locations)
          .filter((value) => !AMAZON_COUNTRY_PRIORITIES.includes(normalizeAmazonCountry(value)))
          .sort((a, b) => a.localeCompare(b, 'ru')),
      ]
    : Object.keys(locations);

  useEffect(() => {
    reset(defaults(sale));
  }, [reset, sale]);

  return (
    <form className="sale-form" id={id} onSubmit={handleSubmit(onSubmit)}>
      <section>
        <h3>1. Товар</h3>
        <div className="erp-form">
          <CatalogCascade
            allowCreate
            brandId={brandId}
            categoryId={categoryId}
            productId={productId}
            inStock
            initialBrand={
              sale?.brand_id
                ? {
                    id: sale.brand_id,
                    name: sale.brand,
                    active: true,
                    product_count: 1,
                  }
                : null
            }
            initialCategory={
              sale?.category_id && sale.brand_id
                ? {
                    id: sale.category_id,
                    brand_id: sale.brand_id,
                    name: sale.category,
                    brand_name: sale.brand,
                    active: true,
                    product_count: 1,
                  }
                : null
            }
            initialProduct={
              sale?.brand_id && sale.category_id
                ? {
                    id: sale.product_id,
                    product_id: sale.product_id,
                    name: sale.product_name,
                    article: '',
                    barcode: sale.barcode,
                    brand_id: sale.brand_id,
                    category_id: sale.category_id,
                    brand: sale.brand,
                    category: sale.category,
                    cell: '',
                    stock: 0,
                    stock_display: '—',
                    active: true,
                  }
                : null
            }
            onBrandChange={(nextBrandId) => {
              setValue('brand_id', nextBrandId as number, {
                shouldValidate: true,
              });
              setValue('category_id', null as unknown as number);
              setValue('product_id', '');
              setValue('product_name', '');
            }}
            onCategoryChange={(nextCategoryId) => {
              setValue('category_id', nextCategoryId as number, {
                shouldValidate: true,
              });
              setValue('product_id', '');
              setValue('product_name', '');
            }}
            onProductChange={(nextProductId, product) => {
              setValue('product_id', nextProductId, { shouldValidate: true });
              setValue('product_name', product?.name ?? sale?.product_name ?? '');
            }}
            onCatalogCreated={onCatalogCreated}
            errors={{
              brand: errors.brand_id?.message,
              category: errors.category_id?.message,
              product: errors.product_id?.message,
            }}
          />
          <input type="hidden" {...register('product_name')} />
        </div>
      </section>
      <section>
        <h3>2. Параметры продажи</h3>
        <div className="erp-form">
          <label className="form-field">
            <span>Дата продажи *</span>
            <input type="datetime-local" {...register('created_at')} />
            {errors.created_at ? <small>{errors.created_at.message}</small> : null}
          </label>
          <Controller
            control={control}
            name="source"
            render={({ field }) => (
              <SearchableSelect
                label="Источник"
                required
                placeholder="Выберите источник"
                options={['Tictactoy', 'Wildberries', 'Amazon', 'Ziiiro сайт'].map((source) => ({
                  value: source,
                  label: source,
                }))}
                value={field.value}
                onChange={field.onChange}
                error={errors.source?.message}
              />
            )}
          />
          <label className="form-field">
            <span>Количество *</span>
            <input type="number" min="1" max="25" {...register('quantity')} />
            {errors.quantity ? <small>{errors.quantity.message}</small> : null}
          </label>
          <label className="form-field">
            <span>Цена продажи *</span>
            <input type="number" min="1" step="0.01" {...register('unit_price')} />
            {errors.unit_price ? <small>{errors.unit_price.message}</small> : null}
          </label>
          <label className="form-field">
            <span>Номер заказа</span>
            <input {...register('order_number')} />
          </label>
          <label className="form-field">
            <span>Статус</span>
            <select {...register('order_status')}>
              <option value="completed">Выполнен</option>
              <option value="processing">В работе</option>
              <option value="shipped">Отправлен</option>
              <option value="returned">Возврат</option>
              <option value="cancelled">Отменён</option>
            </select>
          </label>
        </div>
      </section>
      {isTictactoy ? <section>
        <h3>3. Оплата и комиссия</h3>
        <div className="erp-form">
          <label className="form-field">
            <span>Способ оплаты</span>
            <input {...register('payment_method')} />
          </label>
          <label className="form-field">
            <span>Комиссия, ₽</span>
            <input type="number" min="0" step="0.01" {...register('commission_amount')} />
          </label>
          <input type="hidden" {...register('commission')} />
        </div>
      </section> : null}
      <section>
        <h3>4. Доставка</h3>
        <div className="erp-form">
          {isTictactoy ? <label className="form-field">
            <span>Трекинг</span>
            <input {...register('track_number')} />
          </label> : null}
          <label className="form-field">
            <span>Способ доставки</span>
            <input {...register('delivery_method')} />
          </label>
          <label className="form-field">
            <span>Стоимость доставки</span>
            <input type="number" min="0" step="0.01" {...register('delivery_cost')} />
          </label>
          {isAmazon ? <label className="form-field">
            <span>Трекинг</span>
            <input {...register('invoice_number')} />
          </label> : null}
          {isWildberries ? <label className="form-field">
            <span>Номер стикера</span>
            <input {...register('sticker_number')} />
          </label> : null}
          {isAmazon ? <label className="form-field">
            <span>Платформа</span>
            <input {...register('platform')} />
          </label> : null}
        </div>
      </section>
      <section>
        <h3>5. Получатель и адрес</h3>
        <div className="erp-form">
          <Controller
            control={control}
            name="country"
            render={({ field }) => (
              <SearchableSelect
                label="Страна"
                placeholder="Найдите страну"
                options={countryOptions.map((value) => ({ value, label: value }))}
                value={field.value}
                onChange={(value) => {
                  field.onChange(isAmazon ? normalizeAmazonCountry(value) : value);
                  setValue('region', '');
                  setValue('city', '');
                }}
              />
            )}
          />
          <Controller
            control={control}
            name="region"
            render={({ field }) => (
              <SearchableSelect
                label="Регион"
                placeholder={country ? 'Найдите регион' : 'Сначала выберите страну'}
                options={getOrderedRegions(country, locations).map((value) => ({
                  value,
                  label: value,
                }))}
                value={field.value}
                onChange={(value) => {
                  field.onChange(value);
                  setValue('city', '');
                }}
                disabled={!country}
              />
            )}
          />
          <Controller
            control={control}
            name="city"
            render={({ field }) => (
              <SearchableSelect
                label="Город"
                placeholder={region ? 'Найдите город' : 'Сначала выберите регион'}
                options={(locations[country]?.[region] ?? []).map((value) => ({
                  value,
                  label: value,
                }))}
                value={field.value}
                onChange={field.onChange}
                disabled={!region}
              />
            )}
          />
          <label className="form-field">
            <span>Получатель</span>
            <input {...register('recipient_name')} />
          </label>
          <label className="form-field">
            <span>Телефон / контакт</span>
            <input {...register('recipient')} />
          </label>
          <label className="form-field span-2">
            <span>Адрес</span>
            <input {...register('delivery_address')} />
          </label>
        </div>
      </section>
      <section>
        <h3>6. Примечание</h3>
        <div className="erp-form">
          <label className="form-field span-2">
            <span>Комментарий</span>
            <textarea rows={4} {...register('note')} />
          </label>
        </div>
      </section>
    </form>
  );
}
