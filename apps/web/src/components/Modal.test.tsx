import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Modal } from './Modal';

// Mock focus-trap-react to avoid focus trap issues in test environment
vi.mock('focus-trap-react', () => ({
  FocusTrap: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
}));

describe('Modal', () => {
  const user = userEvent.setup();
  const onClose = vi.fn();

  beforeEach(() => {
    vi.clearAllMocks();
    document.body.className = '';
  });

  it('should not render when isOpen is false', () => {
    render(
      <Modal isOpen={false} onClose={onClose}>
        <div>Modal content</div>
      </Modal>
    );

    expect(screen.queryByText('Modal content')).not.toBeInTheDocument();
  });

  it('should render when isOpen is true', () => {
    render(
      <Modal isOpen={true} onClose={onClose}>
        <div>Modal content</div>
      </Modal>
    );

    expect(screen.getByText('Modal content')).toBeInTheDocument();
  });

  it('should have dialog role and aria-modal attribute', () => {
    render(
      <Modal isOpen={true} onClose={onClose}>
        <div>Content</div>
      </Modal>
    );

    const dialog = screen.getByRole('dialog');
    expect(dialog).toHaveAttribute('aria-modal', 'true');
  });

  it('should call onClose when Escape key is pressed', async () => {
    render(
      <Modal isOpen={true} onClose={onClose}>
        <div>Content</div>
      </Modal>
    );

    await user.keyboard('{Escape}');
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('should not close on Escape when closeOnEscape is false', async () => {
    render(
      <Modal isOpen={true} onClose={onClose} closeOnEscape={false}>
        <div>Content</div>
      </Modal>
    );

    await user.keyboard('{Escape}');
    expect(onClose).not.toHaveBeenCalled();
  });

  it('should call onClose when clicking overlay', async () => {
    render(
      <Modal isOpen={true} onClose={onClose}>
        <div>Content</div>
      </Modal>
    );

    const overlay = screen.getByRole('dialog');
    await user.click(overlay);
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('should not close on overlay click when closeOnOverlayClick is false', async () => {
    render(
      <Modal isOpen={true} onClose={onClose} closeOnOverlayClick={false}>
        <div>Content</div>
      </Modal>
    );

    const overlay = screen.getByRole('dialog');
    await user.click(overlay);
    expect(onClose).not.toHaveBeenCalled();
  });

  it('should not close when clicking modal content', async () => {
    render(
      <Modal isOpen={true} onClose={onClose}>
        <div>Content</div>
      </Modal>
    );

    await user.click(screen.getByText('Content'));
    expect(onClose).not.toHaveBeenCalled();
  });

  it('should add overflow-hidden to body when open', () => {
    render(
      <Modal isOpen={true} onClose={onClose}>
        <div>Content</div>
      </Modal>
    );

    expect(document.body.classList.contains('overflow-hidden')).toBe(true);
  });

  it('should remove overflow-hidden from body when closed', () => {
    const { rerender } = render(
      <Modal isOpen={true} onClose={onClose}>
        <div>Content</div>
      </Modal>
    );

    expect(document.body.classList.contains('overflow-hidden')).toBe(true);

    rerender(
      <Modal isOpen={false} onClose={onClose}>
        <div>Content</div>
      </Modal>
    );

    expect(document.body.classList.contains('overflow-hidden')).toBe(false);
  });
});
