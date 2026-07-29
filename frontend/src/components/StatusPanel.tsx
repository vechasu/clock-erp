import type { PropsWithChildren, ReactNode } from 'react';

interface StatusPanelProps extends PropsWithChildren {
  eyebrow: string;
  title: string;
  actions?: ReactNode;
}

export function StatusPanel({ actions, children, eyebrow, title }: StatusPanelProps) {
  return (
    <section className="foundation-card" aria-labelledby="foundation-title">
      <p className="foundation-eyebrow">{eyebrow}</p>
      <h1 id="foundation-title">{title}</h1>
      <div className="foundation-copy">{children}</div>
      {actions ? <div className="foundation-actions">{actions}</div> : null}
    </section>
  );
}
