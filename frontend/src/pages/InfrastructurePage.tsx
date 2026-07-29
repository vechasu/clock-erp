import { StatusPanel } from '../components/StatusPanel';

export function InfrastructurePage() {
  return (
    <main className="foundation-page">
      <div className="foundation-brand" aria-label="Vechasu ERP">
        <span className="foundation-brand-mark" aria-hidden="true">
          VE
        </span>
        <span>
          <strong>Vechasu ERP</strong>
          <small>параллельный React-контур</small>
        </span>
      </div>

      <StatusPanel
        eyebrow="Этап 1 · инфраструктура"
        title="React-инфраструктура подготовлена"
        actions={
          <a className="foundation-button" href="/">
            Открыть текущий интерфейс
          </a>
        }
      >
        <p>
          Бизнес-модули пока работают только в текущем Flask/Jinja-интерфейсе. Этот маршрут
          изолирован и не меняет существующие данные или пользовательские сценарии.
        </p>
      </StatusPanel>
    </main>
  );
}
