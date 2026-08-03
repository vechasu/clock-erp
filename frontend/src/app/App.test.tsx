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

  it('redirects unknown and retired business modules to products', async () => {
    renderApp('/customers');
    expect(await screen.findByRole('heading', { name: 'Товары' })).toBeInTheDocument();
  });
});
