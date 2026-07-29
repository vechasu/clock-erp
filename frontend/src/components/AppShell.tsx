import { useEffect, useState, type PropsWithChildren } from 'react';
import { NavLink } from 'react-router-dom';

import { Icon, type IconName } from './Icons';

const navigation = [
  { to: '/products', label: 'Товары', icon: 'package' },
  { to: '/sales', label: 'Продажи', icon: 'sales' },
  { to: '/receipts', label: 'Приход', icon: 'receipt' },
  { to: '/repairs', label: 'Ремонт', icon: 'repair' },
] satisfies Array<{ to: string; label: string; icon: IconName }>;

const secondaryNavigation = [
  { href: '/warehouse', label: 'Склад и ячейки', icon: 'warehouse' },
  { href: '/stock-operations', label: 'Операции', icon: 'archive' },
] satisfies Array<{ href: string; label: string; icon: IconName }>;

function Navigation({ onNavigate }: { onNavigate?: () => void }) {
  return (
    <>
      <nav className="erp-nav" aria-label="Основная навигация">
        {navigation.map((item) => (
          <NavLink
            className={({ isActive }) => `erp-nav-link${isActive ? ' is-active' : ''}`}
            key={item.to}
            to={item.to}
            onClick={onNavigate}
          >
            <span className="erp-nav-icon" aria-hidden="true">
              <Icon name={item.icon} />
            </span>
            <span>{item.label}</span>
          </NavLink>
        ))}
      </nav>
      <p className="erp-nav-section">Дополнительно</p>
      <nav className="erp-nav is-secondary" aria-label="Дополнительная навигация">
        {secondaryNavigation.map((item) => (
          <a className="erp-nav-link" href={item.href} key={item.href}>
            <span className="erp-nav-icon" aria-hidden="true">
              <Icon name={item.icon} />
            </span>
            <span>{item.label}</span>
          </a>
        ))}
      </nav>
    </>
  );
}

function Brand() {
  return (
    <NavLink className="erp-logo" to="/" aria-label="Vechasu ERP">
      <span className="erp-logo-mark">VE</span>
      <span className="erp-logo-copy">
        <strong>Vechasu</strong>
        <small>ERP · Tictactoy</small>
      </span>
    </NavLink>
  );
}

function SidebarFooter() {
  return (
    <div className="erp-sidebar-footer">
      <span className="system-state">
        <i aria-hidden="true" />
        Система работает
      </span>
      <a className="erp-legacy-link" href="/settings">
        Настройки
      </a>
    </div>
  );
}

const primaryMobileNavigation = navigation.slice(0, 4);

export function AppShell({ children }: PropsWithChildren) {
  const [mobileOpen, setMobileOpen] = useState(false);
  const [collapsed, setCollapsed] = useState(
    () => window.localStorage.getItem('vechasu:sidebar-collapsed') === '1',
  );

  useEffect(() => {
    window.localStorage.setItem('vechasu:sidebar-collapsed', collapsed ? '1' : '0');
  }, [collapsed]);

  useEffect(() => {
    if (!mobileOpen) return;
    const close = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setMobileOpen(false);
    };
    document.addEventListener('keydown', close);
    return () => document.removeEventListener('keydown', close);
  }, [mobileOpen]);

  return (
    <div className={`erp-shell${collapsed ? ' is-sidebar-collapsed' : ''}`}>
      <a className="skip-link" href="#main-content">
        Перейти к содержимому
      </a>
      <aside className="erp-sidebar">
        <Brand />
        <Navigation />
        <button
          className="sidebar-collapse"
          type="button"
          onClick={() => setCollapsed((value) => !value)}
          aria-label={collapsed ? 'Развернуть боковую панель' : 'Свернуть боковую панель'}
        >
          <Icon name={collapsed ? 'chevronRight' : 'chevronLeft'} />
          <span>{collapsed ? 'Развернуть' : 'Свернуть'}</span>
        </button>
        <SidebarFooter />
      </aside>

      <header className="mobile-topbar">
        <button type="button" onClick={() => setMobileOpen(true)} aria-label="Открыть меню">
          <Icon name="menu" />
        </button>
        <Brand />
        <span
          className="mobile-system-state"
          title="Система работает"
          aria-label="Система работает"
        />
      </header>

      {mobileOpen ? (
        <div className="mobile-drawer-layer">
          <button
            className="mobile-drawer-backdrop"
            type="button"
            onClick={() => setMobileOpen(false)}
            aria-label="Закрыть меню"
          />
          <aside className="mobile-drawer" aria-label="Мобильное меню">
            <div>
              <Brand />
              <button type="button" onClick={() => setMobileOpen(false)} aria-label="Закрыть меню">
                <Icon name="close" />
              </button>
            </div>
            <Navigation onNavigate={() => setMobileOpen(false)} />
            <SidebarFooter />
          </aside>
        </div>
      ) : null}

      <main className="erp-main" id="main-content" tabIndex={-1}>
        {children}
      </main>

      <nav className="mobile-bottom-nav" aria-label="Основная мобильная навигация">
        {primaryMobileNavigation.map((item) => (
          <NavLink
            className={({ isActive }) => (isActive ? 'is-active' : '')}
            key={item.to}
            to={item.to}
          >
            <Icon name={item.icon} />
            <span>{item.label}</span>
          </NavLink>
        ))}
      </nav>
    </div>
  );
}
