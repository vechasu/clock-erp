import { useEffect, useMemo, useState, type PropsWithChildren } from 'react';
import { NavLink } from 'react-router-dom';

import { ERP_NAVIGATION } from '../app/navigation';
import { Icon } from './Icons';

interface BootstrapUser {
  first_name?: string;
  last_name?: string;
  email?: string;
  role?: string;
}

interface BootstrapData {
  user?: BootstrapUser | null;
  csrf_token?: string;
}

function readBootstrapData(): BootstrapData {
  const encoded = document.getElementById('root')?.dataset.bootstrap;
  if (!encoded || encoded.startsWith('__')) return {};
  try {
    return JSON.parse(decodeURIComponent(escape(window.atob(encoded)))) as BootstrapData;
  } catch {
    return {};
  }
}

function Navigation({ onNavigate }: { onNavigate?: () => void }) {
  return (
    <nav className="erp-nav" aria-label="Основная навигация">
      {ERP_NAVIGATION.map((item) => (
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
  );
}

function Brand() {
  return (
    <NavLink className="erp-logo" to="/products" aria-label="Vechasu ERP">
      <span className="erp-logo-mark">VE</span>
      <span className="erp-logo-copy">
        <strong>Vechasu</strong>
        <small>ERP · Tictactoy</small>
      </span>
    </NavLink>
  );
}

function SidebarFooter({ bootstrap }: { bootstrap: BootstrapData }) {
  const name = [bootstrap.user?.first_name, bootstrap.user?.last_name].filter(Boolean).join(' ');
  const displayName = name || bootstrap.user?.email || 'Пользователь';
  const initials = name
    ? name
        .split(/\s+/)
        .slice(0, 2)
        .map((part) => part[0])
        .join('')
        .toLocaleUpperCase('ru-RU')
    : 'П';

  return (
    <div className="erp-sidebar-footer">
      <span className="system-state">
        <i aria-hidden="true" />
        Система работает
      </span>
      <div className="erp-user-profile" aria-label="Профиль пользователя">
        <span aria-hidden="true">{initials}</span>
        <div>
          <strong>{displayName}</strong>
          {bootstrap.user?.email && name ? <small>{bootstrap.user.email}</small> : null}
        </div>
      </div>
      <form className="erp-logout" action="/logout" method="post">
        <input type="hidden" name="csrf_token" value={bootstrap.csrf_token ?? ''} />
        <button type="submit">Выйти</button>
      </form>
    </div>
  );
}

export function ErpShell({ children }: PropsWithChildren) {
  const bootstrap = useMemo(readBootstrapData, []);
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
        <SidebarFooter bootstrap={bootstrap} />
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
            <SidebarFooter bootstrap={bootstrap} />
          </aside>
        </div>
      ) : null}

      <main className="erp-main" id="main-content" tabIndex={-1}>
        {children}
      </main>

      <nav className="mobile-bottom-nav" aria-label="Основная мобильная навигация">
        {ERP_NAVIGATION.map((item) => (
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
