import type { IconName } from '../components/Icons';

export const ERP_NAVIGATION = [
  { to: '/products', label: 'Товары', icon: 'package' },
  { to: '/sales', label: 'Продажи', icon: 'sales' },
  { to: '/receipts', label: 'Приход', icon: 'receipt' },
  { to: '/settings', label: 'Настройки', icon: 'settings' },
] satisfies Array<{ to: string; label: string; icon: IconName }>;
