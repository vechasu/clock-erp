import { zodResolver } from '@hookform/resolvers/zod';
import { Controller, useForm } from 'react-hook-form';

import { SearchableSelect } from '../../components/Controls';
import {
  saleFormSchema,
  type Sale,
  type SaleCatalogProduct,
  type SaleFormInput,
  type SaleFormValues,
  type SaleLocations,
} from './schemas';

interface SaleFormProps {
  id: string;
  sale?: Sale | null;
  products: SaleCatalogProduct[];
  locations: SaleLocations;
  onSubmit: (values: SaleFormValues) => void;
}

function defaults(sale?: Sale | null): SaleFormInput {
  return {
    created_at: sale?.created_at.slice(0, 10) || new Date().toISOString().slice(0, 10),
    source: sale?.source || 'Tictactoy',
    product_id: sale?.product_id || '',
    product_name: sale?.product_name || '',
    quantity: sale?.quantity || 1,
    unit_price: sale?.unit_price || 1,
    order_number: sale?.order_number || '',
    order_status: sale?.order_status || 'completed',
    track_number: sale?.track_number || '',
    delivery_method: sale?.delivery_method || '',
    delivery_cost: sale?.delivery_cost || 0,
    country: sale?.country || '',
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

export function SaleForm({ id, sale, products, locations, onSubmit }: SaleFormProps) {
  const {
    control,
    register,
    setValue,
    watch,
    handleSubmit,
    formState: { errors },
  } = useForm<SaleFormInput, unknown, SaleFormValues>({
    resolver: zodResolver(saleFormSchema),
    defaultValues: defaults(sale),
  });
  const productId = watch('product_id');
  const country = watch('country');
  const region = watch('region');
  const selected = products.find((product) => product.id === productId);
  const quantityLocked = Boolean(sale?.inventory_managed);

  return (
    <form className="sale-form" id={id} onSubmit={handleSubmit(onSubmit)}>
      <section>
        <h3>1. Товар</h3>
        <div className="erp-form">
          <Controller
            control={control}
            name="product_id"
            render={({ field }) => (
              <div className="form-field span-2">
                <SearchableSelect
                  label="Товар"
                  required
                  placeholder="Название, артикул или бренд"
                  options={[
                    ...products.map((product) => ({
                      value: product.id,
                      label: `${product.name} · ${product.brand} · доступно ${product.stock_display}`,
                      disabled: product.stock <= 0 && product.id !== sale?.product_id,
                    })),
                    ...(sale && !products.some((product) => product.id === sale.product_id)
                      ? [{ value: sale.product_id, label: sale.product_name }]
                      : []),
                  ]}
                  value={field.value}
                  onChange={(value) => {
                    field.onChange(value);
                    const product = products.find((item) => item.id === value);
                    setValue('product_name', product?.name ?? sale?.product_name ?? '');
                  }}
                  disabled={quantityLocked}
                  error={errors.product_id?.message}
                  hint={
                    selected
                      ? [selected.article, selected.brand, selected.category]
                          .filter(Boolean)
                          .join(' · ')
                      : undefined
                  }
                />
                <input type="hidden" {...register('product_name')} />
              </div>
            )}
          />
        </div>
      </section>
      <section>
        <h3>2. Параметры продажи</h3>
        <div className="erp-form">
          <label className="form-field">
            <span>Дата продажи *</span>
            <input type="date" {...register('created_at')} />
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
            <input
              type="number"
              min="1"
              max="25"
              {...register('quantity')}
              readOnly={quantityLocked}
            />
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
              <option value="cancelled">Отменён</option>
            </select>
          </label>
        </div>
      </section>
      <section>
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
          <label className="form-field">
            <span>Платформа</span>
            <input {...register('platform')} />
          </label>
          <label className="form-field">
            <span>Номер стикера</span>
            <input {...register('sticker_number')} />
          </label>
        </div>
      </section>
      <section>
        <h3>4. Доставка</h3>
        <div className="erp-form">
          <label className="form-field">
            <span>Трек-номер</span>
            <input {...register('track_number')} />
          </label>
          <label className="form-field">
            <span>Способ доставки</span>
            <input {...register('delivery_method')} />
          </label>
          <label className="form-field">
            <span>Стоимость доставки</span>
            <input type="number" min="0" step="0.01" {...register('delivery_cost')} />
          </label>
          <label className="form-field">
            <span>Номер накладной</span>
            <input {...register('invoice_number')} />
          </label>
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
                options={Object.keys(locations).map((value) => ({ value, label: value }))}
                value={field.value}
                onChange={(value) => {
                  field.onChange(value);
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
                options={Object.keys(locations[country] ?? {}).map((value) => ({
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
