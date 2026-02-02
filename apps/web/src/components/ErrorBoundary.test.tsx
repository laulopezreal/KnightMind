import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ErrorBoundary } from './ErrorBoundary';

// Mutable flag to control throwing behavior from outside
let shouldComponentThrow = true;

function ThrowingComponent() {
  if (shouldComponentThrow) {
    throw new Error('Test error message');
  }
  return <div>Child content</div>;
}

describe('ErrorBoundary', () => {
  const user = userEvent.setup();

  beforeEach(() => {
    // Suppress console.error from React error boundary
    vi.spyOn(console, 'error').mockImplementation(() => {});
    shouldComponentThrow = true;
  });

  it('should render children when there is no error', () => {
    shouldComponentThrow = false;

    render(
      <ErrorBoundary>
        <ThrowingComponent />
      </ErrorBoundary>
    );

    expect(screen.getByText('Child content')).toBeInTheDocument();
  });

  it('should render default fallback UI when child throws', () => {
    render(
      <ErrorBoundary>
        <ThrowingComponent />
      </ErrorBoundary>
    );

    expect(screen.getByText('Something went wrong')).toBeInTheDocument();
    expect(screen.getByText('Test error message')).toBeInTheDocument();
  });

  it('should render custom fallback when provided', () => {
    render(
      <ErrorBoundary fallback={<div>Custom error UI</div>}>
        <ThrowingComponent />
      </ErrorBoundary>
    );

    expect(screen.getByText('Custom error UI')).toBeInTheDocument();
    expect(screen.queryByText('Something went wrong')).not.toBeInTheDocument();
  });

  it('should call onError callback when error occurs', () => {
    const onError = vi.fn();

    render(
      <ErrorBoundary onError={onError}>
        <ThrowingComponent />
      </ErrorBoundary>
    );

    expect(onError).toHaveBeenCalledWith(
      expect.any(Error),
      expect.objectContaining({ componentStack: expect.any(String) })
    );
  });

  it('should have a "Try Again" button that resets error state', async () => {
    render(
      <ErrorBoundary>
        <ThrowingComponent />
      </ErrorBoundary>
    );

    expect(screen.getByText('Something went wrong')).toBeInTheDocument();

    // Stop throwing before clicking Try Again so re-render succeeds
    shouldComponentThrow = false;

    const tryAgainButton = screen.getByRole('button', { name: /try to recover from error/i });
    await user.click(tryAgainButton);

    expect(screen.getByText('Child content')).toBeInTheDocument();
  });

  it('should have a "Reload Page" button', () => {
    render(
      <ErrorBoundary>
        <ThrowingComponent />
      </ErrorBoundary>
    );

    const reloadButton = screen.getByRole('button', { name: /reload the page/i });
    expect(reloadButton).toBeInTheDocument();
  });

  it('should show generic message when error has no message', () => {
    function ThrowGeneric(): React.ReactNode {
      throw new Error();
    }

    render(
      <ErrorBoundary>
        <ThrowGeneric />
      </ErrorBoundary>
    );

    expect(screen.getByText('An unexpected error occurred')).toBeInTheDocument();
  });
});
