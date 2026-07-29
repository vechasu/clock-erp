import type { PropsWithChildren } from 'react';
import { NavLink } from 'react-router-dom';

const navigation = [
  { to: '/products', label: 'Товары', symbol: 'Т' },
  { to: '/receipts', label: 'Приходы', symbol: 'П' },
  { to: '/sales', label: 'Продажи', symbol: '₽' },
];

export function AppShell({ children }: PropsWithChildren) {
  return (
    <div className="erp-shell">
      <aside className="erp-sidebar">
        <NavLink className="erp-logo" to="/" aria-label="Vechasu ERP">
          <span className="erp-logo-mark">VE</span>
          <span className="erp-logo-copy">
            <strong>Vechasu</strong>
            <small>ERP</small>
          </span>
        </NavLink>
        <nav className="erp-nav" aria-label="Основная навигация">
          {navigation.map((item) => (
            <NavLink
              className={({ isActive }) => `erp-nav-link${isActive ? ' is-active' : ''}`}
              key={item.to}
              to={item.to}
            >
              <span className="erp-nav-icon" aria-hidden="true">
                {item.symbol}
              </span>
              <span>{item.label}</span>
            </NavLink>
          ))}
        </nav>
        <a className="erp-legacy-link" href="/">
          Текущий интерфейс
        </a>
      </aside>
      <main className="erp-main">{children}</main>
    </div>
  );
}
