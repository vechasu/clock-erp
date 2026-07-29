interface ToastProps {
  message: string;
  kind?: 'success' | 'error';
  onClose: () => void;
}

export function Toast({ message, kind = 'success', onClose }: ToastProps) {
  return (
    <div className={`erp-toast is-${kind}`} role={kind === 'error' ? 'alert' : 'status'}>
      <span aria-hidden="true">
        <Icon name={kind === 'success' ? 'check' : 'alert'} />
      </span>
      <p>{message}</p>
      <button type="button" onClick={onClose} aria-label="Закрыть уведомление">
        <Icon name="close" />
      </button>
    </div>
  );
}
import { Icon } from './Icons';
