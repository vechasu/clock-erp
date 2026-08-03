import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';

import { AppProviders } from '../../app/providers';
import { SettingsPage } from './SettingsPage';

function envelope(data: unknown) {
  return { data, meta: { request_id: 'settings-test', csrf_token: 'csrf' }, error: null };
}

describe('SettingsPage', () => {
  afterEach(() => vi.unstubAllGlobals());

  it('loads and updates only current ERP settings', async () => {
    const settings = {
      company_name: 'Tictactoy',
      erp_name: 'Vechasu ERP',
      low_stock_threshold: 3,
    };
    const fetchMock = vi.fn().mockImplementation((_input: RequestInfo | URL, init?: RequestInit) =>
      Promise.resolve(
        new Response(
          JSON.stringify(
            envelope(init?.method === 'PATCH' ? JSON.parse(String(init.body)) : settings),
          ),
          {
            status: 200,
            headers: { 'Content-Type': 'application/json' },
          },
        ),
      ),
    );
    vi.stubGlobal('fetch', fetchMock);
    const user = userEvent.setup();
    render(
      <MemoryRouter initialEntries={['/settings']}>
        <AppProviders>
          <SettingsPage />
        </AppProviders>
      </MemoryRouter>,
    );

    expect(await screen.findByDisplayValue('Tictactoy')).toBeInTheDocument();
    expect(screen.queryByText('Управление вкладками')).not.toBeInTheDocument();
    await user.clear(screen.getByLabelText('Название компании'));
    await user.type(screen.getByLabelText('Название компании'), 'Tictactoy Group');
    await user.click(screen.getByRole('button', { name: 'Сохранить настройки' }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    expect(fetchMock.mock.calls[1]?.[1]).toMatchObject({ method: 'PATCH' });
    expect(await screen.findByText('Настройки сохранены')).toBeInTheDocument();
  });
});
