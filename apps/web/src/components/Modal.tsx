import { useEffect, useRef } from 'react';
import type { ReactNode } from 'react';
import { FocusTrap } from 'focus-trap-react';

interface ModalProps {
  isOpen: boolean;
  onClose: () => void;
  children: ReactNode;
  closeOnEscape?: boolean;
  closeOnOverlayClick?: boolean;
}

/**
 * Reusable modal component with proper accessibility
 * - Escape key to close
 * - Overlay click to close
 * - Focus trap
 * - ARIA attributes for screen readers
 */
export function Modal({
  isOpen,
  onClose,
  children,
  closeOnEscape = true,
  closeOnOverlayClick = true
}: ModalProps) {
  const modalRef = useRef<HTMLDivElement>(null);
  const previousFocusRef = useRef<HTMLElement | null>(null);

  // Handle escape key
  useEffect(() => {
    if (!isOpen || !closeOnEscape) return;

    const handleEscape = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        onClose();
      }
    };

    document.addEventListener('keydown', handleEscape);
    return () => document.removeEventListener('keydown', handleEscape);
  }, [isOpen, closeOnEscape, onClose]);

  // Focus management
  useEffect(() => {
    if (isOpen) {
      // Save current focus
      previousFocusRef.current = document.activeElement as HTMLElement;

      // Focus modal
      if (modalRef.current) {
        modalRef.current.focus();
      }

      // Prevent body scroll
      document.body.style.overflow = 'hidden';
    } else {
      // Restore focus when modal closes
      if (previousFocusRef.current) {
        previousFocusRef.current.focus();
      }

      // Restore body scroll
      document.body.style.overflow = '';
    }

    return () => {
      document.body.style.overflow = '';
    };
  }, [isOpen]);

  if (!isOpen) return null;

  return (
    <FocusTrap
      active={isOpen}
      focusTrapOptions={{
        allowOutsideClick: true,
        escapeDeactivates: false,
        initialFocus: () => modalRef.current || undefined,
      }}
    >
      <div
        className="fixed inset-0 flex items-center justify-center bg-black/50 z-50 animate-teedin"
        onClick={(e) => {
          if (closeOnOverlayClick && e.target === e.currentTarget) {
            onClose();
          }
        }}
        role="dialog"
        aria-modal="true"
      >
        <div
          ref={modalRef}
          className="relative"
          tabIndex={-1}
        >
          {children}
        </div>
      </div>
    </FocusTrap>
  );
}
