import type { ReactNode } from 'react';

import { Icon } from './Icons';

interface PageStateProps {
  title: string;
  message: string;
  action?: ReactNode;
  kind?: 'empty' | 'error';
}

export function PageState({ title, message, action, kind = 'empty' }: PageStateProps) {
  return (
    <div className={`page-state is-${kind}`} role={kind === 'error' ? 'alert' : 'status'}>
      <span className="page-state-icon" aria-hidden="true">
        <Icon name={kind === 'error' ? 'alert' : 'empty'} />
      </span>
      <h2>{title}</h2>
      <p>{message}</p>
      {action}
    </div>
  );
}
