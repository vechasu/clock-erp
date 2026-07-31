import { useEffect, useId, useRef, type PropsWithChildren, type ReactNode } from 'react';

import { Icon } from './Icons';

interface ModalProps extends PropsWithChildren {
  open: boolean;
  title: string;
  description?: string;
  onClose: () => void;
  footer?: ReactNode;
  size?: 'medium' | 'large' | 'wide';
  closeLabel?: string;
  lazy?: boolean;
}

export function Modal({
  open,
  title,
  description,
  onClose,
  footer,
  children,
  size = 'medium',
  closeLabel = 'Закрыть',
  lazy = false,
}: ModalProps) {
  const titleId = useId();
  const dialogRef = useRef<HTMLDialogElement>(null);

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;
    const previousOverflow = document.body.style.overflow;
    if (open && !dialog.open) {
      dialog.showModal();
      document.body.style.overflow = 'hidden';
    }
    if (!open && dialog.open) {
      dialog.close();
      document.body.style.overflow = previousOverflow;
    }
    return () => {
      document.body.style.overflow = previousOverflow;
    };
  }, [open]);

  if (lazy && !open) return null;

  return (
    <dialog
      className={`erp-modal is-${size}`}
      ref={dialogRef}
      aria-labelledby={titleId}
      onCancel={(event) => {
        event.preventDefault();
        onClose();
      }}
      onClose={onClose}
    >
      <div className="erp-modal-header">
        <div>
          <h2 id={titleId}>{title}</h2>
          {description ? <p>{description}</p> : null}
        </div>
        <button className="icon-button" type="button" onClick={onClose} aria-label={closeLabel}>
          <Icon name="close" />
        </button>
      </div>
      <div className="erp-modal-body">{children}</div>
      {footer ? <div className="erp-modal-footer">{footer}</div> : null}
    </dialog>
  );
}

export function ModalHeader({ children }: PropsWithChildren) {
  return <div className="modal-section-header">{children}</div>;
}

export function ModalFooter({ children }: PropsWithChildren) {
  return <div className="modal-section-footer">{children}</div>;
}

interface ConfirmDialogProps {
  open: boolean;
  title: string;
  message: string;
  confirmLabel?: string;
  pendingLabel?: string;
  pending?: boolean;
  onConfirm: () => void;
  onClose: () => void;
}

export function ConfirmDialog({
  open,
  title,
  message,
  confirmLabel = 'Удалить',
  pendingLabel = 'Удаляем…',
  pending,
  onConfirm,
  onClose,
}: ConfirmDialogProps) {
  return (
    <Modal
      open={open}
      title={title}
      description={message}
      onClose={onClose}
      footer={
        <>
          <button className="button secondary" type="button" onClick={onClose}>
            Отмена
          </button>
          <button className="button danger" type="button" onClick={onConfirm} disabled={pending}>
            {pending ? pendingLabel : confirmLabel}
          </button>
        </>
      }
    >
      <div className="confirm-illustration" aria-hidden="true">
        <Icon name="alert" />
      </div>
    </Modal>
  );
}
