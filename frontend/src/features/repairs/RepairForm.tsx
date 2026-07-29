import { zodResolver } from '@hookform/resolvers/zod';
import { useEffect } from 'react';
import { Controller, useForm } from 'react-hook-form';

import { SearchableSelect } from '../../components/Controls';
import {
  repairFormSchema,
  type Repair,
  type RepairCatalogItem,
  type RepairFormValues,
} from './schemas';

interface Facet {
  value: string;
  label: string;
}

interface RepairFormProps {
  id: string;
  repair?: Repair | null;
  products: RepairCatalogItem[];
  statuses: Facet[];
  types: Facet[];
  locations: Facet[];
  channels: Facet[];
  onSubmit: (values: RepairFormValues) => void;
}

function defaults(repair?: Repair | null): RepairFormValues {
  return {
    status: repair?.status ?? 'new',
    request_type: repair?.request_type ?? 'paid_repair',
    responsible: repair?.responsible ?? '',
    order_number: repair?.order_number ?? '',
    order_source: repair?.order_source ?? 'none',
    client_name: repair?.client_name ?? '',
    client_phone: repair?.client_phone ?? '',
    client_email: repair?.client_email ?? '',
    client_messenger: repair?.client_messenger ?? '',
    product_id: repair?.product_id ?? '',
    product_name: repair?.product_name ?? '',
    brand: repair?.brand ?? '',
    model: repair?.model ?? '',
    article: repair?.article ?? '',
    serial_number: repair?.serial_number ?? '',
    equipment: repair?.equipment ?? '',
    communication_channel: repair?.communication_channel ?? 'other',
    contact: repair?.contact ?? '',
    problem: repair?.problem ?? '',
    diagnostic_result: repair?.diagnostic_result ?? '',
    master_conclusion: repair?.master_conclusion ?? '',
    decision: repair?.decision ?? '',
    estimate_cost: repair?.estimate_cost ?? '',
    final_cost: repair?.final_cost ?? '',
    master: repair?.master ?? '',
    location: repair?.location ?? 'unknown',
    request_at: repair?.request_at ?? new Date().toISOString().slice(0, 10),
    customer_sent_at: repair?.customer_sent_at ?? '',
    accepted_at: repair?.accepted_at ?? '',
    master_handoff_at: repair?.master_handoff_at ?? '',
    repair_completed_at: repair?.repair_completed_at ?? '',
    returned_at: repair?.returned_at ?? '',
    due_date: repair?.due_date ?? '',
    communication: repair?.communication ?? '',
    internal_comment: repair?.internal_comment ?? '',
    event_comment: '',
  };
}

export function RepairForm({
  id,
  repair,
  products,
  statuses,
  types,
  locations,
  channels,
  onSubmit,
}: RepairFormProps) {
  const {
    control,
    register,
    reset,
    setValue,
    handleSubmit,
    formState: { errors },
  } = useForm<RepairFormValues>({
    resolver: zodResolver(repairFormSchema),
    defaultValues: defaults(repair),
  });

  useEffect(() => {
    reset(defaults(repair));
  }, [repair, reset]);

  return (
    <form className="sale-form repair-form" id={id} onSubmit={handleSubmit(onSubmit)}>
      <section>
        <h3>Обращение</h3>
        <div className="erp-form">
          <label className="form-field">
            <span>Статус *</span>
            <select {...register('status')}>
              {statuses.map((item) => (
                <option key={item.value} value={item.value}>
                  {item.label}
                </option>
              ))}
            </select>
          </label>
          <label className="form-field">
            <span>Тип обращения *</span>
            <select {...register('request_type')}>
              {types.map((item) => (
                <option key={item.value} value={item.value}>
                  {item.label}
                </option>
              ))}
            </select>
          </label>
          <label className="form-field">
            <span>Дата обращения</span>
            <input type="date" {...register('request_at')} />
          </label>
          <label className="form-field">
            <span>Ответственный</span>
            <input {...register('responsible')} placeholder="Сотрудник" />
          </label>
          <label className="form-field">
            <span>Номер заказа</span>
            <input {...register('order_number')} />
          </label>
          <label className="form-field">
            <span>Источник заказа</span>
            <select {...register('order_source')}>
              <option value="none">Без заказа</option>
              <option value="our">Наш магазин</option>
              <option value="external">Внешний</option>
            </select>
          </label>
        </div>
      </section>

      <section>
        <h3>Клиент и связь</h3>
        <div className="erp-form">
          <label className="form-field span-2">
            <span>Имя клиента *</span>
            <input {...register('client_name')} aria-invalid={Boolean(errors.client_name)} />
            {errors.client_name ? <small>{errors.client_name.message}</small> : null}
          </label>
          <label className="form-field">
            <span>Телефон</span>
            <input type="tel" {...register('client_phone')} />
          </label>
          <label className="form-field">
            <span>Почта</span>
            <input type="email" {...register('client_email')} />
            {errors.client_email ? <small>{errors.client_email.message}</small> : null}
          </label>
          <label className="form-field">
            <span>Мессенджер</span>
            <input {...register('client_messenger')} />
          </label>
          <label className="form-field">
            <span>Канал связи</span>
            <select {...register('communication_channel')}>
              {channels.map((item) => (
                <option key={item.value} value={item.value}>
                  {item.label}
                </option>
              ))}
            </select>
          </label>
          <label className="form-field span-2">
            <span>Основной контакт</span>
            <input {...register('contact')} />
          </label>
        </div>
      </section>

      <section>
        <h3>Товар</h3>
        <div className="erp-form">
          <Controller
            control={control}
            name="product_id"
            render={({ field }) => (
              <div className="form-field span-2">
                <SearchableSelect
                  label="Товар из каталога"
                  placeholder="Название, бренд или артикул"
                  options={[
                    ...products.map((product) => ({
                      value: product.id,
                      label: [product.name, product.brand, product.article]
                        .filter(Boolean)
                        .join(' · '),
                    })),
                    ...(repair?.product_id &&
                    !products.some((product) => product.id === repair.product_id)
                      ? [{ value: repair.product_id, label: repair.product_name }]
                      : []),
                  ]}
                  value={field.value}
                  onChange={(value) => {
                    field.onChange(value);
                    const product = products.find((item) => item.id === value);
                    if (product) {
                      setValue('product_name', product.name);
                      setValue('brand', product.brand);
                      setValue('model', product.model);
                      setValue('article', product.article);
                    }
                  }}
                  hint="Связанные данные заполнятся автоматически"
                />
              </div>
            )}
          />
          <label className="form-field span-2">
            <span>Название товара *</span>
            <input {...register('product_name')} aria-invalid={Boolean(errors.product_name)} />
            {errors.product_name ? <small>{errors.product_name.message}</small> : null}
          </label>
          <label className="form-field">
            <span>Бренд</span>
            <input {...register('brand')} />
          </label>
          <label className="form-field">
            <span>Модель</span>
            <input {...register('model')} />
          </label>
          <label className="form-field">
            <span>Артикул</span>
            <input {...register('article')} />
          </label>
          <label className="form-field">
            <span>Серийный номер</span>
            <input {...register('serial_number')} />
          </label>
          <label className="form-field span-2">
            <span>Комплектация</span>
            <input {...register('equipment')} />
          </label>
        </div>
      </section>

      <section>
        <h3>Диагностика и решение</h3>
        <div className="erp-form">
          <label className="form-field span-2">
            <span>Неисправность *</span>
            <textarea rows={3} {...register('problem')} aria-invalid={Boolean(errors.problem)} />
            {errors.problem ? <small>{errors.problem.message}</small> : null}
          </label>
          <label className="form-field span-2">
            <span>Результат диагностики</span>
            <textarea rows={3} {...register('diagnostic_result')} />
          </label>
          <label className="form-field span-2">
            <span>Заключение мастера</span>
            <textarea rows={3} {...register('master_conclusion')} />
          </label>
          <label className="form-field span-2">
            <span>Решение</span>
            <textarea rows={2} {...register('decision')} />
          </label>
          <label className="form-field">
            <span>Предварительная стоимость</span>
            <input inputMode="decimal" {...register('estimate_cost')} />
          </label>
          <label className="form-field">
            <span>Итоговая стоимость</span>
            <input inputMode="decimal" {...register('final_cost')} />
          </label>
          <label className="form-field">
            <span>Мастер</span>
            <input {...register('master')} />
          </label>
          <label className="form-field">
            <span>Местонахождение</span>
            <select {...register('location')}>
              {locations.map((item) => (
                <option key={item.value} value={item.value}>
                  {item.label}
                </option>
              ))}
            </select>
          </label>
        </div>
      </section>

      <section>
        <h3>Сроки</h3>
        <div className="erp-form">
          {[
            ['customer_sent_at', 'Клиент отправил'],
            ['accepted_at', 'Принято у нас'],
            ['master_handoff_at', 'Передано мастеру'],
            ['repair_completed_at', 'Ремонт завершён'],
            ['returned_at', 'Возвращено клиенту'],
            ['due_date', 'Плановый срок'],
          ].map(([name, label]) => (
            <label className="form-field" key={name}>
              <span>{label}</span>
              <input type="date" {...register(name as keyof RepairFormValues)} />
            </label>
          ))}
        </div>
      </section>

      <section>
        <h3>Комментарии</h3>
        <div className="erp-form">
          <label className="form-field span-2">
            <span>История общения</span>
            <textarea rows={3} {...register('communication')} />
          </label>
          <label className="form-field span-2">
            <span>Внутренний комментарий</span>
            <textarea rows={3} {...register('internal_comment')} />
          </label>
          <label className="form-field span-2">
            <span>Комментарий к изменению</span>
            <input {...register('event_comment')} />
          </label>
        </div>
      </section>
    </form>
  );
}
