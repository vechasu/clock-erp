import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useState } from 'react';

import { SearchableSelect } from './Controls';

function Harness({ onQueryChange }: { onQueryChange?: (query: string) => void }) {
  const [value, setValue] = useState('');
  return (
    <SearchableSelect
      label="Товар"
      placeholder="Найдите товар"
      options={[
        { value: '1', label: 'Casio G-Shock' },
        { value: '2', label: 'Vechasu Voyager' },
      ]}
      value={value}
      onChange={setValue}
      onQueryChange={onQueryChange}
    />
  );
}

describe('SearchableSelect', () => {
  it('filters after every character, supports keyboard choice and clears only itself', async () => {
    const onQueryChange = vi.fn();
    const user = userEvent.setup();
    render(<Harness onQueryChange={onQueryChange} />);

    const input = screen.getByRole('combobox', { name: 'Товар' });
    await user.type(input, 'voy');
    expect(onQueryChange.mock.calls.map(([query]) => query)).toEqual(['v', 'vo', 'voy']);
    expect(screen.getByText('Найдено: 1')).toBeInTheDocument();
    expect(screen.getByRole('option', { name: 'Vechasu Voyager' })).toBeInTheDocument();

    await user.keyboard('{Enter}');
    expect(input).toHaveValue('Vechasu Voyager');
    await user.click(screen.getByRole('button', { name: 'Очистить поле «Товар»' }));
    expect(input).toHaveValue('');
    expect(onQueryChange).toHaveBeenLastCalledWith('');
  });
});
