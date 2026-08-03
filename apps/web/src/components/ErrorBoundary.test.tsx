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
  let user: ReturnType<typeof userEvent.setup>;

  beforeEach(() => {
    user = userEvent.setup();
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

  it('should render fallback as render prop with error and reset access', async () => {
    render(
      <ErrorBoundary
        fallback={({ error, reset }) => (
          <div>
            <span>Caught: {error?.message}</span>
            <button onClick={reset}>Custom Reset</button>
          </div>
        )}
      >
        <ThrowingComponent />
      </ErrorBoundary>
    );

    expect(screen.getByText('Caught: Test error message')).toBeInTheDocument();
    expect(screen.queryByText('Something went wrong')).not.toBeInTheDocument();

    shouldComponentThrow = false;
    await user.click(screen.getByText('Custom Reset'));

    expect(screen.getByText('Child content')).toBeInTheDocument();
  });

  // A caught boundary renders its fallback until something resets it — React
  // never retries by itself. `resetKey` is how a page-scoped boundary learns
  // the user has navigated away from the page that threw.
  it('should clear a caught error when resetKey changes', () => {
    const { rerender } = render(
      <ErrorBoundary resetKey="/dashboard">
        <ThrowingComponent />
      </ErrorBoundary>
    );

    expect(screen.getByText('Something went wrong')).toBeInTheDocument();

    shouldComponentThrow = false;
    rerender(
      <ErrorBoundary resetKey="/insights">
        <ThrowingComponent />
      </ErrorBoundary>
    );

    expect(screen.getByText('Child content')).toBeInTheDocument();
  });

  it('should keep showing the fallback while resetKey is unchanged', () => {
    const { rerender } = render(
      <ErrorBoundary resetKey="/dashboard">
        <ThrowingComponent />
      </ErrorBoundary>
    );

    // Re-render on the same key with a child that would now succeed: staying on
    // the page that threw must not silently re-run the render that failed.
    shouldComponentThrow = false;
    rerender(
      <ErrorBoundary resetKey="/dashboard">
        <ThrowingComponent />
      </ErrorBoundary>
    );

    expect(screen.getByText('Something went wrong')).toBeInTheDocument();
    expect(screen.queryByText('Child content')).not.toBeInTheDocument();
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
