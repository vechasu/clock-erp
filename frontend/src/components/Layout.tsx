import type { PropsWithChildren, ReactNode } from 'react';

import { Icon, type IconName } from './Icons';

interface PageHeaderProps {
  eyebrow?: string;
  title: string;
  description: string;
  actions?: ReactNode;
}

export function PageHeader({ eyebrow, title, description, actions }: PageHeaderProps) {
  return (
    <header className="page-header">
      <div className="page-heading">
        {eyebrow ? <p className="page-eyebrow">{eyebrow}</p> : null}
        <h1>{title}</h1>
        <p>{description}</p>
      </div>
      {actions ? <div className="header-actions">{actions}</div> : null}
    </header>
  );
}

export interface StatItem {
  label: string;
  value: ReactNode;
  hint?: string;
  tone?: 'default' | 'success' | 'warning' | 'danger' | 'info' | 'purple';
}

export function StatsGrid({
  items,
  label,
  loading = false,
}: {
  items: StatItem[];
  label: string;
  loading?: boolean;
}) {
  return (
    <section className="summary-grid" aria-label={label} aria-busy={loading}>
      {items.map((item) => (
        <article className={`stat-card is-${item.tone ?? 'default'}`} key={item.label}>
          <span>{item.label}</span>
          {loading ? <i className="skeleton stat-skeleton" /> : <strong>{item.value}</strong>}
          {item.hint ? <small>{item.hint}</small> : null}
        </article>
      ))}
    </section>
  );
}

export function Toolbar({ children }: PropsWithChildren) {
  return <div className="list-toolbar">{children}</div>;
}

export function Tabs({
  label,
  items,
  active,
  onChange,
}: {
  label: string;
  items: Array<{ key: string; label: string; count?: number }>;
  active: string;
  onChange: (key: string) => void;
}) {
  return (
    <div className="source-tabs" role="tablist" aria-label={label}>
      {items.map((item) => (
        <button
          key={item.key}
          role="tab"
          type="button"
          aria-selected={active === item.key}
          className={active === item.key ? 'is-active' : ''}
          onClick={() => onChange(item.key)}
        >
          {item.label}
          {typeof item.count === 'number' ? <span>{item.count}</span> : null}
        </button>
      ))}
    </div>
  );
}

type ButtonTone = 'primary' | 'secondary' | 'danger' | 'ghost';

interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  tone?: ButtonTone;
  icon?: IconName;
}

export function Button({
  tone = 'secondary',
  icon,
  children,
  className = '',
  ...props
}: ButtonProps) {
  return (
    <button className={`button ${tone} ${className}`.trim()} {...props}>
      {icon ? <Icon name={icon} /> : null}
      <span>{children}</span>
    </button>
  );
}

export function ActionLink({
  href,
  children,
  icon,
  tone = 'secondary',
}: {
  href: string;
  children: ReactNode;
  icon?: IconName;
  tone?: ButtonTone;
}) {
  return (
    <a className={`button ${tone}`} href={href}>
      {icon ? <Icon name={icon} /> : null}
      <span>{children}</span>
    </a>
  );
}

export function StatusBadge({
  label,
  tone = 'info',
}: {
  label: string;
  tone?: 'neutral' | 'success' | 'warning' | 'danger' | 'info' | 'purple';
}) {
  return <span className={`status-badge is-${tone}`}>{label}</span>;
}

export function SourceBadge({ source }: { source: string }) {
  const key = source.toLocaleLowerCase('ru-RU').replace(/\s+/g, '-');
  return <span className={`source-badge is-${key}`}>{source || '—'}</span>;
}

export function LoadingState({ label = 'Загружаем данные…' }: { label?: string }) {
  return (
    <div className="loading-state" role="status" aria-label={label}>
      <span className="visually-hidden">{label}</span>
      {Array.from({ length: 7 }, (_, index) => (
        <i className="skeleton table-skeleton" key={index} />
      ))}
    </div>
  );
}

export function BulkActionBar({
  count,
  children,
  onClear,
}: PropsWithChildren<{ count: number; onClear: () => void }>) {
  if (!count) return null;
  return (
    <div className="bulk-action-bar" role="status">
      <strong>Выбрано: {count}</strong>
      <div>{children}</div>
      <button type="button" onClick={onClear}>
        Снять выбор
      </button>
    </div>
  );
}
