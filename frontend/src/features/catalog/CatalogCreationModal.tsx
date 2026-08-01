import { useMutation, useQueryClient } from '@tanstack/react-query';
import { createPortal } from 'react-dom';
import { useEffect, useState } from 'react';

import { ApiRequestError } from '../../api/client';
import { ImageUploader, type EncodedImage } from '../../components/ImageUploader';
import { Modal } from '../../components/Modal';
import { createCatalogBrand, createCatalogCategory, createCatalogProduct } from './api';
import type { CatalogBrand, CatalogCategory, CatalogProduct } from './schemas';

export interface CatalogCreationRequest {
  kind: 'brand' | 'category' | 'product';
  brandId?: number;
  categoryId?: number;
}

export type CatalogCreatedEntity =
  | { kind: 'brand'; value: CatalogBrand }
  | { kind: 'category'; value: CatalogCategory }
  | { kind: 'product'; value: CatalogProduct };

interface CatalogCreationModalProps {
  request: CatalogCreationRequest | null;
  onClose: () => void;
  onCreated: (created: CatalogCreatedEntity, message: string) => void;
}

export function CatalogCreationModal({ request, onClose, onCreated }: CatalogCreationModalProps) {
  const queryClient = useQueryClient();
  const [name, setName] = useState('');
  const [article, setArticle] = useState('');
  const [productImage, setProductImage] = useState<EncodedImage | null>(null);

  useEffect(() => {
    setName('');
    setArticle('');
    setProductImage(null);
  }, [request]);

  const mutation = useMutation({
    mutationFn: async () => {
      if (!request) throw new Error('Не выбран тип справочника');
      if (request.kind === 'brand') {
        return { kind: 'brand' as const, value: await createCatalogBrand(name) };
      }
      if (request.kind === 'category') {
        if (!request.brandId) throw new Error('Сначала выберите бренд');
        return {
          kind: 'category' as const,
          value: await createCatalogCategory(request.brandId, name),
        };
      }
      if (!request.brandId || !request.categoryId) {
        throw new Error('Сначала выберите бренд и категорию');
      }
      return {
        kind: 'product' as const,
        value: await createCatalogProduct({
          name,
          article,
          brand_id: request.brandId,
          category_id: request.categoryId,
          product_image: productImage,
        }),
      };
    },
    onSuccess: async (created) => {
      await queryClient.invalidateQueries({ queryKey: ['catalog-options'] });
      await queryClient.invalidateQueries({ queryKey: ['products'] });
      onCreated(
        created,
        created.kind === 'brand'
          ? 'Бренд создан'
          : created.kind === 'category'
            ? 'Категория создана'
            : 'Товар создан и доступен во всех трёх разделах',
      );
      onClose();
    },
  });

  const title =
    request?.kind === 'brand'
      ? 'Новый бренд'
      : request?.kind === 'category'
        ? 'Новая категория'
        : 'Новый товар';
  const error =
    mutation.error instanceof ApiRequestError
      ? mutation.error.message
      : mutation.error instanceof Error
        ? mutation.error.message
        : '';

  if (!request || typeof document === 'undefined') return null;

  return createPortal(
    <Modal
      open
      title={title}
      description="Значение сохраняется в едином справочнике Vechasu ERP."
      onClose={onClose}
      footer={
        <>
          <button className="button secondary" type="button" onClick={onClose}>
            Отмена
          </button>
          <button
            className="button primary"
            type="button"
            disabled={!name.trim() || mutation.isPending}
            onClick={() => mutation.mutate()}
          >
            {mutation.isPending ? 'Создаём…' : 'Создать'}
          </button>
        </>
      }
    >
      <div className="erp-form">
        <label className="form-field span-2">
          <span>Название *</span>
          <input value={name} onChange={(event) => setName(event.target.value)} />
        </label>
        {request?.kind === 'product' ? (
          <>
            <label className="form-field">
              <span>Артикул</span>
              <input value={article} onChange={(event) => setArticle(event.target.value)} />
            </label>
            <ImageUploader label="Фото нового товара" onChange={setProductImage} />
          </>
        ) : null}
        {error ? <p className="form-error">{error}</p> : null}
      </div>
    </Modal>,
    document.body,
  );
}
