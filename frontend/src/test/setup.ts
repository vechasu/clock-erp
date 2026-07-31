import '@testing-library/jest-dom/vitest';
import { cleanup } from '@testing-library/react';
import { afterEach } from 'vitest';

if (!HTMLDialogElement.prototype.showModal) {
  HTMLDialogElement.prototype.showModal = function showModal() {
    this.setAttribute('open', '');
  };
}

if (!HTMLDialogElement.prototype.close) {
  HTMLDialogElement.prototype.close = function close() {
    this.removeAttribute('open');
  };
}

if (!URL.createObjectURL) {
  URL.createObjectURL = () => 'blob:vechasu-test-preview';
}

if (!URL.revokeObjectURL) {
  URL.revokeObjectURL = () => undefined;
}

afterEach(() => {
  cleanup();
});
