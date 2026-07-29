import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import { Button, PageHeader, StatsGrid, Tabs } from './Layout';

describe('shared ERP layout components', () => {
  it('keeps page hierarchy, stats and tabs accessible', async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(
      <>
        <PageHeader
          title="Товары"
          description="Единый каталог"
          actions={<Button icon="plus">Добавить</Button>}
        />
        <StatsGrid label="Сводка" items={[{ label: 'Позиций', value: 42 }]} />
        <Tabs
          label="Виды"
          active="active"
          items={[
            { key: 'active', label: 'Активные' },
            { key: 'archive', label: 'Архив' },
          ]}
          onChange={onChange}
        />
      </>,
    );

    expect(screen.getByRole('heading', { name: 'Товары' })).toBeInTheDocument();
    expect(screen.getByRole('region', { name: 'Сводка' })).toHaveTextContent('42');
    expect(screen.getByRole('tab', { name: 'Активные' })).toHaveAttribute('aria-selected', 'true');
    await user.click(screen.getByRole('tab', { name: 'Архив' }));
    expect(onChange).toHaveBeenCalledWith('archive');
  });
});
