import type {
  ChangeEventHandler,
  InputHTMLAttributes,
  PropsWithChildren,
  SelectHTMLAttributes,
} from 'react';

interface LiveSearchProps {
  label: string;
  placeholder: string;
  value: string;
  onChange: (value: string) => void;
}

export function LiveSearch({ label, placeholder, value, onChange }: LiveSearchProps) {
  return (
    <label className="search-control">
      <span aria-hidden="true">⌕</span>
      <span className="visually-hidden">{label}</span>
      <input
        value={value}
        onChange={(event) => onChange(event.target.value)}
        placeholder={placeholder}
      />
    </label>
  );
}

export interface EntityOption {
  value: string;
  label: string;
  disabled?: boolean;
}

interface EntitySelectProps extends SelectHTMLAttributes<HTMLSelectElement> {
  label: string;
  options: EntityOption[];
  placeholder: string;
}

export function EntitySelect({
  label,
  options,
  placeholder,
  ...selectProps
}: EntitySelectProps) {
  return (
    <label className="form-field">
      <span>{label}</span>
      <select {...selectProps}>
        <option value="">{placeholder}</option>
        {options.map((option) => (
          <option key={option.value} value={option.value} disabled={option.disabled}>
            {option.label}
          </option>
        ))}
      </select>
    </label>
  );
}

export const BrandSelect = EntitySelect;
export const CategorySelect = EntitySelect;
export const ProductSelect = EntitySelect;

interface DateRangePickerProps {
  from: string;
  to: string;
  onFromChange: (value: string) => void;
  onToChange: (value: string) => void;
}

export function DateRangePicker({
  from,
  to,
  onFromChange,
  onToChange,
}: DateRangePickerProps) {
  return (
    <>
      <label>
        Дата с
        <input
          type="date"
          value={from}
          max={to || undefined}
          onChange={(event) => onFromChange(event.target.value)}
        />
      </label>
      <label>
        по
        <input
          type="date"
          value={to}
          min={from || undefined}
          onChange={(event) => onToChange(event.target.value)}
        />
      </label>
    </>
  );
}

export function FilterPanel({ children }: PropsWithChildren) {
  return (
    <details className="filter-panel">
      <summary>Фильтры</summary>
      <div className="filter-grid">{children}</div>
    </details>
  );
}

export function ActionMenu({ children }: PropsWithChildren) {
  return <div className="row-actions">{children}</div>;
}

type ActionButtonProps = {
  onClick: () => void;
  disabled?: boolean;
  title?: string;
};

export function EditButton({ onClick, disabled, title }: ActionButtonProps) {
  return (
    <button type="button" onClick={onClick} disabled={disabled} title={title}>
      Изменить
    </button>
  );
}

export function DeleteButton({ onClick, disabled, title }: ActionButtonProps) {
  return (
    <button
      className="danger-link"
      type="button"
      onClick={onClick}
      disabled={disabled}
      title={title}
    >
      Удалить
    </button>
  );
}

export type NativeSelectChange = ChangeEventHandler<HTMLSelectElement>;
export type NativeInputProps = InputHTMLAttributes<HTMLInputElement>;
