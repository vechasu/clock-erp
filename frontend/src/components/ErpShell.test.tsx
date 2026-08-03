import { render, screen, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

import { ERP_NAVIGATION } from '../app/navigation';
import { ErpShell } from './ErpShell';

describe('ErpShell navigation contract', () => {
  it('contains exactly the four active ERP sections and account controls', () => {
    render(
      <MemoryRouter initialEntries={['/products']}>
        <ErpShell>
          <h1>Товары</h1>
        </ErpShell>
      </MemoryRouter>,
    );

    expect(ERP_NAVIGATION.map(({ label }) => label)).toEqual([
      'Товары',
      'Продажи',
      'Приход',
      'Настройки',
    ]);
    const sidebar = screen.getByRole('complementary');
    const navigation = within(sidebar).getByRole('navigation', { name: 'Основная навигация' });
    expect(
      within(navigation)
        .getAllByRole('link')
        .map((link) => link.textContent),
    ).toEqual(['Товары', 'Продажи', 'Приход', 'Настройки']);
    expect(screen.queryByText('Ремонт')).not.toBeInTheDocument();
    expect(screen.queryByText('Склад и ячейки')).not.toBeInTheDocument();
    expect(screen.queryByText('Операции')).not.toBeInTheDocument();
    expect(within(sidebar).getByLabelText('Профиль пользователя')).toBeInTheDocument();
    expect(within(sidebar).getByText('Система работает')).toBeInTheDocument();
    expect(within(sidebar).getByRole('button', { name: 'Выйти' })).toBeInTheDocument();
  });
});
