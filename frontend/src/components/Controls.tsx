import type {
  ChangeEventHandler,
  InputHTMLAttributes,
  PropsWithChildren,
  SelectHTMLAttributes,
} from 'react';
import { useEffect, useId, useMemo, useRef, useState } from 'react';

import { Icon } from './Icons';

interface LiveSearchProps {
  label: string;
  placeholder: string;
  value: string;
  onChange: (value: string) => void;
}

export function LiveSearch({ label, placeholder, value, onChange }: LiveSearchProps) {
  return (
    <label className="search-control">
      <Icon name="search" />
      <span className="visually-hidden">{label}</span>
      <input
        value={value}
        onChange={(event) => onChange(event.target.value)}
        placeholder={placeholder}
      />
      {value ? (
        <button type="button" onClick={() => onChange('')} aria-label={`Очистить: ${label}`}>
          <Icon name="close" />
        </button>
      ) : null}
    </label>
  );
}

export interface EntityOption {
  value: string;
  label: string;
  inputLabel?: string;
  description?: string;
  meta?: string;
  searchText?: string;
  disabled?: boolean;
}

interface EntitySelectProps extends SelectHTMLAttributes<HTMLSelectElement> {
  label: string;
  options: EntityOption[];
  placeholder: string;
}

export function EntitySelect({ label, options, placeholder, ...selectProps }: EntitySelectProps) {
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

interface SearchableSelectProps {
  label: string;
  options: EntityOption[];
  placeholder: string;
  value: string;
  onChange: (value: string) => void;
  disabled?: boolean;
  required?: boolean;
  error?: string;
  hint?: string;
  loading?: boolean;
  emptyLabel?: string;
  onQueryChange?: (query: string) => void;
  createAction?: {
    label: string;
    onClick: () => void;
  };
}

export function SearchableSelect({
  label,
  options,
  placeholder,
  value,
  onChange,
  disabled = false,
  required = false,
  error,
  hint,
  loading = false,
  emptyLabel = 'Ничего не найдено',
  onQueryChange,
  createAction,
}: SearchableSelectProps) {
  const id = useId();
  const listboxId = `${id}-listbox`;
  const rootRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const selected = options.find((option) => option.value === value);
  const [query, setQuery] = useState(selected?.inputLabel ?? selected?.label ?? '');
  const [open, setOpen] = useState(false);
  const [activeIndex, setActiveIndex] = useState(0);

  useEffect(() => {
    setQuery(selected?.inputLabel ?? selected?.label ?? '');
  }, [selected?.inputLabel, selected?.label]);

  useEffect(() => {
    const close = (event: PointerEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener('pointerdown', close);
    return () => document.removeEventListener('pointerdown', close);
  }, []);

  const visibleOptions = useMemo(() => {
    const normalized = query.trim().toLocaleLowerCase('ru-RU');
    if (!normalized || selected?.inputLabel === query || selected?.label === query) {
      return options.slice(0, 100);
    }
    return options
      .filter((option) =>
        option.label.toLocaleLowerCase('ru-RU').includes(normalized) ||
        (option.searchText ?? [option.description, option.meta].filter(Boolean).join(' '))
          .toLocaleLowerCase('ru-RU')
          .includes(normalized),
      )
      .slice(0, 100);
  }, [options, query, selected?.inputLabel, selected?.label]);

  const choose = (option: EntityOption) => {
    if (option.disabled) return;
    onChange(option.value);
    setQuery(option.inputLabel ?? option.label);
    setOpen(false);
    inputRef.current?.focus();
  };

  return (
    <div className={`form-field searchable-select${error ? ' has-error' : ''}`} ref={rootRef}>
      <label htmlFor={id}>
        {label}
        {required ? ' *' : ''}
      </label>
      <div className="searchable-select-control">
        <input
          id={id}
          ref={inputRef}
          type="text"
          role="combobox"
          autoComplete="off"
          aria-autocomplete="list"
          aria-controls={listboxId}
          aria-expanded={open}
          aria-activedescendant={
            open && visibleOptions[activeIndex] ? `${listboxId}-${activeIndex}` : undefined
          }
          aria-invalid={Boolean(error)}
          value={query}
          placeholder={placeholder}
          disabled={disabled}
          onFocus={() => setOpen(true)}
          onChange={(event) => {
            setQuery(event.target.value);
            onQueryChange?.(event.target.value);
            setActiveIndex(0);
            setOpen(true);
            if (value) onChange('');
          }}
          onKeyDown={(event) => {
            if (event.key === 'ArrowDown') {
              event.preventDefault();
              setOpen(true);
              setActiveIndex((index) => Math.min(index + 1, visibleOptions.length - 1));
            } else if (event.key === 'ArrowUp') {
              event.preventDefault();
              setActiveIndex((index) => Math.max(index - 1, 0));
            } else if (event.key === 'Enter' && open && visibleOptions[activeIndex]) {
              event.preventDefault();
              choose(visibleOptions[activeIndex]);
            } else if (event.key === 'Escape') {
              setOpen(false);
            }
          }}
        />
        {value ? (
          <button
            type="button"
            onClick={() => {
              onChange('');
              setQuery('');
              onQueryChange?.('');
              inputRef.current?.focus();
            }}
            aria-label={`Очистить поле «${label}»`}
          >
            <Icon name="close" />
          </button>
        ) : (
          <Icon name="chevronDown" />
        )}
      </div>
      {open && !disabled ? (
        <div className="searchable-select-popover">
          <div className="searchable-select-count" aria-live="polite">
            {loading ? 'Загрузка…' : `Найдено: ${visibleOptions.length}`}
            {options.length > 100 && visibleOptions.length === 100 ? ' (показаны первые 100)' : ''}
          </div>
          <ul id={listboxId} role="listbox">
            {visibleOptions.map((option, index) => (
              <li
                id={`${listboxId}-${index}`}
                key={option.value}
                role="option"
                aria-selected={option.value === value}
                aria-disabled={option.disabled}
                className={`${index === activeIndex ? 'is-active' : ''}${
                  option.disabled ? ' is-disabled' : ''
                }`}
                onMouseDown={(event) => {
                  event.preventDefault();
                  choose(option);
                }}
                onMouseEnter={() => setActiveIndex(index)}
              >
                <span className="searchable-select-option" title={option.label}>
                  <strong>{option.label}</strong>
                  {option.description ? <small>{option.description}</small> : null}
                </span>
                {option.meta ? <span className="searchable-select-meta">{option.meta}</span> : null}
                {option.value === value ? <Icon name="check" /> : null}
              </li>
            ))}
            {!visibleOptions.length ? (
              <li className="is-empty" role="option" aria-disabled="true" aria-selected="false">
                {loading ? 'Загружаем значения…' : emptyLabel}
              </li>
            ) : null}
          </ul>
          {createAction ? (
            <button
              className="searchable-select-create"
              type="button"
              onMouseDown={(event) => event.preventDefault()}
              onClick={() => {
                setOpen(false);
                createAction.onClick();
              }}
            >
              {createAction.label}
            </button>
          ) : null}
        </div>
      ) : null}
      {hint && !error ? <small className="field-hint">{hint}</small> : null}
      {error ? <small className="form-error">{error}</small> : null}
    </div>
  );
}

interface DateRangePickerProps {
  from: string;
  to: string;
  onFromChange: (value: string) => void;
  onToChange: (value: string) => void;
}

export function DateRangePicker({ from, to, onFromChange, onToChange }: DateRangePickerProps) {
  return (
    <div className="date-range-control">
      <label>
        <span>Дата с</span>
        <input
          type="date"
          value={from}
          max={to || undefined}
          onChange={(event) => onFromChange(event.target.value)}
        />
      </label>
      <label>
        <span>по</span>
        <input
          type="date"
          value={to}
          min={from || undefined}
          onChange={(event) => onToChange(event.target.value)}
        />
      </label>
    </div>
  );
}

export function FilterPanel({
  children,
  count = 0,
  lazy = false,
}: PropsWithChildren<{ count?: number; lazy?: boolean }>) {
  const [openedOnce, setOpenedOnce] = useState(count > 0);
  return (
    <details
      className="filter-panel"
      onToggle={(event) => {
        if (event.currentTarget.open) setOpenedOnce(true);
      }}
    >
      <summary>
        <Icon name="filter" />
        Фильтры
        {count ? <span className="filter-count">{count}</span> : null}
      </summary>
      {!lazy || openedOnce || count > 0 ? <div className="filter-grid">{children}</div> : null}
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
