import { useEffect, useRef, useState, type DragEvent } from 'react';

import { Icon } from './Icons';

interface FileUploadProps {
  label: string;
  accept: string[];
  maxSize: number;
  value: File | null;
  onChange: (file: File | null) => void;
  onErrorChange?: (message: string) => void;
  disabled?: boolean;
  compactImage?: boolean;
}

function formatSize(value: number) {
  if (value < 1024 * 1024) return `${Math.ceil(value / 1024)} КБ`;
  return `${(value / (1024 * 1024)).toLocaleString('ru-RU', {
    maximumFractionDigits: 1,
  })} МБ`;
}

export function FileUpload({
  label,
  accept,
  maxSize,
  value,
  onChange,
  onErrorChange,
  disabled = false,
  compactImage = false,
}: FileUploadProps) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [error, setError] = useState('');
  const [dragging, setDragging] = useState(false);
  const [preview, setPreview] = useState('');

  useEffect(() => {
    if (!value?.type.startsWith('image/')) {
      setPreview('');
      return;
    }
    const url = URL.createObjectURL(value);
    setPreview(url);
    return () => URL.revokeObjectURL(url);
  }, [value]);

  const setValidationError = (message: string) => {
    setError(message);
    onErrorChange?.(message);
  };

  const clear = () => {
    onChange(null);
    setValidationError('');
    if (inputRef.current) inputRef.current.value = '';
  };

  const select = (file: File | undefined) => {
    if (!file) return;
    const extension = file.name.split('.').pop()?.toLocaleLowerCase('ru-RU') ?? '';
    const allowedTypes = accept.filter((item) => item.includes('/'));
    const allowedExtensions = accept
      .filter((item) => item.startsWith('.'))
      .map((item) => item.slice(1).toLocaleLowerCase('ru-RU'));
    const allowed =
      accept.includes('*/*') ||
      (file.type && allowedTypes.length
        ? allowedTypes.includes(file.type)
        : allowedExtensions.includes(extension));
    if (!allowed) {
      setValidationError('Допустимые форматы: JPG, JPEG, PNG, WEBP');
      if (inputRef.current) inputRef.current.value = '';
      return;
    }
    if (file.size > maxSize) {
      setValidationError(`Максимальный размер файла — ${formatSize(maxSize)}`);
      if (inputRef.current) inputRef.current.value = '';
      return;
    }
    setValidationError('');
    onChange(file);
  };

  const dropEvents = {
    onDragEnter: (event: DragEvent<HTMLButtonElement>) => {
      event.preventDefault();
      if (!disabled) setDragging(true);
    },
    onDragOver: (event: DragEvent<HTMLButtonElement>) => event.preventDefault(),
    onDragLeave: () => setDragging(false),
    onDrop: (event: DragEvent<HTMLButtonElement>) => {
      event.preventDefault();
      setDragging(false);
      if (!disabled) select(event.dataTransfer.files[0]);
    },
  };

  return (
    <div className={`file-upload${compactImage ? ' is-image-compact' : ''}`}>
      <span>{label}</span>
      {compactImage && value ? (
        <div className="file-upload-compact-preview">
          <button
            type="button"
            onClick={() => inputRef.current?.click()}
            disabled={disabled}
            aria-label="Заменить выбранное фото"
            title="Заменить фото"
          >
            {preview ? <img src={preview} alt={`Предпросмотр ${value.name}`} /> : null}
          </button>
          <button
            className="file-upload-compact-remove"
            type="button"
            onClick={clear}
            disabled={disabled}
            aria-label="Удалить выбранное фото"
            title="Удалить фото"
          >
            <Icon name="close" />
          </button>
        </div>
      ) : value ? (
        <div className="file-upload-selected">
          {preview ? (
            <img src={preview} alt={`Предпросмотр ${value.name}`} />
          ) : (
            <Icon name="receipt" />
          )}
          <div>
            <strong>{value.name}</strong>
            <small>{formatSize(value.size)}</small>
          </div>
          <button type="button" onClick={() => inputRef.current?.click()} disabled={disabled}>
            Заменить
          </button>
          <button
            type="button"
            onClick={clear}
            disabled={disabled}
            aria-label={`Удалить файл ${value.name}`}
          >
            <Icon name="trash" />
          </button>
        </div>
      ) : compactImage ? (
        <button
          className={`file-upload-compact-dropzone${dragging ? ' is-dragging' : ''}`}
          type="button"
          disabled={disabled}
          onClick={() => inputRef.current?.click()}
          {...dropEvents}
        >
          <Icon name="upload" />
          <span className="visually-hidden">
            Перетащите фото сюда или выберите файл. JPG, JPEG, PNG или WEBP до {formatSize(maxSize)}
            .
          </span>
        </button>
      ) : (
        <button
          className={`file-upload-dropzone${dragging ? ' is-dragging' : ''}`}
          type="button"
          disabled={disabled}
          onClick={() => inputRef.current?.click()}
          {...dropEvents}
        >
          <Icon name="upload" />
          <strong>Перетащите файл сюда или выберите на компьютере</strong>
          <small>
            {accept.join(', ')} · до {formatSize(maxSize)}
          </small>
        </button>
      )}
      <input
        className="visually-hidden"
        ref={inputRef}
        type="file"
        aria-label={label}
        accept={accept.join(',')}
        disabled={disabled}
        onChange={(event) => select(event.target.files?.[0])}
      />
      {error ? (
        <small className="form-error" role="alert">
          {error}
        </small>
      ) : null}
    </div>
  );
}
