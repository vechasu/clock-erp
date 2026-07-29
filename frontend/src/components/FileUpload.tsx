import { useEffect, useRef, useState } from 'react';

import { Icon } from './Icons';

interface FileUploadProps {
  label: string;
  accept: string[];
  maxSize: number;
  value: File | null;
  onChange: (file: File | null) => void;
  disabled?: boolean;
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
  disabled = false,
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

  const select = (file: File | undefined) => {
    if (!file) return;
    const extension = file.name.split('.').pop()?.toLocaleLowerCase('ru-RU') ?? '';
    const allowed = accept.some((item) => {
      const normalized = item.replace(/^\./, '').toLocaleLowerCase('ru-RU');
      return normalized === extension || item === file.type || item === '*/*';
    });
    if (!allowed) {
      setError(`Допустимые форматы: ${accept.join(', ')}`);
      onChange(null);
      return;
    }
    if (file.size > maxSize) {
      setError(`Максимальный размер файла — ${formatSize(maxSize)}`);
      onChange(null);
      return;
    }
    setError('');
    onChange(file);
  };

  return (
    <div className="file-upload">
      <span>{label}</span>
      {value ? (
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
            onClick={() => {
              onChange(null);
              if (inputRef.current) inputRef.current.value = '';
            }}
            disabled={disabled}
            aria-label={`Удалить файл ${value.name}`}
          >
            <Icon name="trash" />
          </button>
        </div>
      ) : (
        <button
          className={`file-upload-dropzone${dragging ? ' is-dragging' : ''}`}
          type="button"
          disabled={disabled}
          onClick={() => inputRef.current?.click()}
          onDragEnter={(event) => {
            event.preventDefault();
            setDragging(true);
          }}
          onDragOver={(event) => event.preventDefault()}
          onDragLeave={() => setDragging(false)}
          onDrop={(event) => {
            event.preventDefault();
            setDragging(false);
            select(event.dataTransfer.files[0]);
          }}
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
