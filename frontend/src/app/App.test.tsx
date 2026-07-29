import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

import { App } from './App';
import { AppProviders } from './providers';

function renderApp(initialEntry = '/') {
  return render(
    <MemoryRouter initialEntries={[initialEntry]}>
      <AppProviders>
        <App />
      </AppProviders>
    </MemoryRouter>,
  );
}

describe('React infrastructure', () => {
  it('exposes the guarded parallel entry point', () => {
    renderApp();

    expect(
      screen.getByRole('heading', { name: 'React-инфраструктура подготовлена' }),
    ).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Открыть текущий интерфейс' })).toHaveAttribute(
      'href',
      '/',
    );
  });

  it('keeps a controlled fallback for unknown business modules', () => {
    renderApp('/customers');

    expect(screen.getByRole('heading', { name: 'Раздел ещё не перенесён' })).toBeInTheDocument();
  });
});
