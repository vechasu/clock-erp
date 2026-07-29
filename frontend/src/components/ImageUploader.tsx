import { useRef, useState } from 'react';

export interface EncodedImage {
  name: string;
  type: string;
  base64: string;
}

interface ImageUploaderProps {
  label?: string;
  onChange: (image: EncodedImage | null) => void;
}

export function ImageUploader({ label = 'Фото товара', onChange }: ImageUploaderProps) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [preview, setPreview] = useState('');
  const [error, setError] = useState('');

  const clear = () => {
    setPreview('');
    setError('');
    if (inputRef.current) inputRef.current.value = '';
    onChange(null);
  };

  return (
    <div className="image-uploader">
      <span>{label}</span>
      {preview ? (
        <div className="image-preview">
          <img src={preview} alt="Предпросмотр загружаемого товара" />
          <button type="button" onClick={clear} aria-label="Удалить выбранное фото">
            ×
          </button>
        </div>
      ) : (
        <button type="button" className="image-dropzone" onClick={() => inputRef.current?.click()}>
          <strong>Выбрать JPEG или PNG</strong>
          <small>До 3 МБ. Фото не заменяет уже существующее.</small>
        </button>
      )}
      <input
        ref={inputRef}
        className="visually-hidden"
        type="file"
        accept="image/jpeg,image/png"
        onChange={(event) => {
          const file = event.target.files?.[0];
          if (!file) return;
          if (!['image/jpeg', 'image/png'].includes(file.type) || file.size > 3 * 1024 * 1024) {
            clear();
            setError('Выберите JPEG или PNG размером до 3 МБ');
            return;
          }
          const reader = new FileReader();
          reader.onload = () => {
            const result = String(reader.result || '');
            setPreview(result);
            setError('');
            onChange({
              name: file.name,
              type: file.type,
              base64: result.split(',', 2)[1] ?? '',
            });
          };
          reader.readAsDataURL(file);
        }}
      />
      {error ? <small className="form-error">{error}</small> : null}
    </div>
  );
}
