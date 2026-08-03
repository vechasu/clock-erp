import { zodResolver } from '@hookform/resolvers/zod';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useState } from 'react';
import { useForm } from 'react-hook-form';

import { ErpShell } from '../../components/ErpShell';
import { Button, LoadingState, PageHeader } from '../../components/Layout';
import { PageState } from '../../components/PageState';
import { Toast } from '../../components/Toast';
import { fetchSettings, updateSettings } from './api';
import { settingsSchema, type Settings } from './schemas';

export function SettingsPage() {
  const queryClient = useQueryClient();
  const [toast, setToast] = useState<{ message: string; kind: 'success' | 'error' } | null>(null);
  const settingsQuery = useQuery({ queryKey: ['settings'], queryFn: fetchSettings });
  const form = useForm<Settings>({
    resolver: zodResolver(settingsSchema),
    defaultValues: { company_name: '', erp_name: '', low_stock_threshold: 0 },
  });

  useEffect(() => {
    if (settingsQuery.data) form.reset(settingsQuery.data);
  }, [form, settingsQuery.data]);

  const mutation = useMutation({
    mutationFn: updateSettings,
    onSuccess: (settings) => {
      queryClient.setQueryData(['settings'], settings);
      form.reset(settings);
      setToast({ message: 'Настройки сохранены', kind: 'success' });
    },
    onError: (error: Error) => setToast({ message: error.message, kind: 'error' }),
  });

  return (
    <ErpShell>
      <div className="erp-page settings-page">
        <PageHeader
          eyebrow="Система"
          title="Настройки"
          description="Основные параметры ERP и контроля остатков"
        />

        {settingsQuery.isPending ? <LoadingState label="Загружаем настройки…" /> : null}
        {settingsQuery.isError ? (
          <PageState
            kind="error"
            title="Настройки не загрузились"
            message={settingsQuery.error.message}
            action={<Button onClick={() => settingsQuery.refetch()}>Повторить</Button>}
          />
        ) : null}
        {settingsQuery.data ? (
          <form
            className="workspace-card settings-form"
            onSubmit={form.handleSubmit((values) => mutation.mutate(values))}
          >
            <div className="settings-form-grid">
              <label className="form-field">
                <span>Название компании</span>
                <input {...form.register('company_name')} placeholder="Tictactoy" />
                {form.formState.errors.company_name ? (
                  <small className="field-error">
                    {form.formState.errors.company_name.message}
                  </small>
                ) : null}
              </label>
              <label className="form-field">
                <span>Название ERP</span>
                <input {...form.register('erp_name')} placeholder="Vechasu ERP" />
                {form.formState.errors.erp_name ? (
                  <small className="field-error">{form.formState.errors.erp_name.message}</small>
                ) : null}
              </label>
              <label className="form-field">
                <span>Порог низкого остатка</span>
                <input
                  type="number"
                  min="0"
                  max="999"
                  {...form.register('low_stock_threshold', { valueAsNumber: true })}
                />
                {form.formState.errors.low_stock_threshold ? (
                  <small className="field-error">
                    {form.formState.errors.low_stock_threshold.message}
                  </small>
                ) : null}
              </label>
            </div>
            <div className="settings-form-actions">
              <Button
                tone="primary"
                type="submit"
                disabled={mutation.isPending || !form.formState.isDirty}
              >
                {mutation.isPending ? 'Сохраняем…' : 'Сохранить настройки'}
              </Button>
            </div>
          </form>
        ) : null}
      </div>
      {toast ? <Toast {...toast} onClose={() => setToast(null)} /> : null}
    </ErpShell>
  );
}
