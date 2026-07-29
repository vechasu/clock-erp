import { Link } from 'react-router-dom';

import { StatusPanel } from '../components/StatusPanel';

export function NotMigratedPage() {
  return (
    <main className="foundation-page">
      <StatusPanel
        eyebrow="Защищённый fallback"
        title="Раздел ещё не перенесён"
        actions={
          <Link className="foundation-button" to="/">
            Вернуться к инфраструктуре
          </Link>
        }
      >
        <p>React-маршрут не активирован. Используйте соответствующую страницу текущей системы.</p>
      </StatusPanel>
    </main>
  );
}
