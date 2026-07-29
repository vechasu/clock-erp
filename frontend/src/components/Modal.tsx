import { useEffect, useId, useRef, type PropsWithChildren, type ReactNode } from 'react';

interface ModalProps extends PropsWithChildren {
  open: boolean;
  title: string;
  description?: string;
  onClose: () => void;
  footer?: ReactNode;
  size?: 'medium' | 'large';
}

export function Modal({
  open,
  title,
  description,
  onClose,
  footer,
  children,
  size = 'medium',
}: ModalProps) {
  const titleId = useId();
  const dialogRef = useRef<HTMLDialogElement>(null);

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;
    if (open && !dialog.open) dialog.showModal();
    if (!open && dialog.open) dialog.close();
  }, [open]);

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
        <button className="icon-button" type="button" onClick={onClose} aria-label="Закрыть">
          ×
        </button>
      </div>
      <div className="erp-modal-body">{children}</div>
      {footer ? <div className="erp-modal-footer">{footer}</div> : null}
    </dialog>
  );
}

interface ConfirmDialogProps {
  open: boolean;
  title: string;
  message: string;
  confirmLabel?: string;
  pending?: boolean;
  onConfirm: () => void;
  onClose: () => void;
}

export function ConfirmDialog({
  open,
  title,
  message,
  confirmLabel = 'Удалить',
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
            {pending ? 'Удаляем…' : confirmLabel}
          </button>
        </>
      }
    >
      <div className="confirm-illustration" aria-hidden="true">
        !
      </div>
    </Modal>
  );
}
