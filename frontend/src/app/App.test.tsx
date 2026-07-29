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
  it('opens the unified products workspace by default', async () => {
    renderApp();

    expect(await screen.findByRole('heading', { name: 'Товары' })).toBeInTheDocument();
  });

  it('keeps a controlled fallback for unknown business modules', () => {
    renderApp('/customers');

    expect(screen.getByRole('heading', { name: 'Раздел ещё не перенесён' })).toBeInTheDocument();
  });
});
