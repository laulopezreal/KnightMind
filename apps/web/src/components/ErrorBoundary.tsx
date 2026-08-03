import { Component } from 'react';
import type { ReactNode, ErrorInfo } from 'react';

interface Props {
  children: ReactNode;
  fallback?: ReactNode | ((props: { error: Error | null; reset: () => void }) => ReactNode);
  onError?: (error: Error, errorInfo: ErrorInfo) => void;
  /**
   * Clears a caught error when this value changes — e.g. the current pathname,
   * so navigating away from a page that threw shows the new page rather than
   * the old page's fallback. Unset means the boundary only resets on demand.
   */
  resetKey?: string | number;
}

interface State {
  hasError: boolean;
  error: Error | null;
}

/**
 * Error Boundary component to catch React errors and display fallback UI
 * Prevents full app crashes and provides graceful error handling
 *
 * Usage:
 *   <ErrorBoundary fallback={<CustomErrorUI />}>
 *     <YourComponent />
 *   </ErrorBoundary>
 */
export class ErrorBoundary extends Component<Props, State> {
  constructor(props: Props) {
    super(props);
    this.state = { hasError: false, error: null };
  }

  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error, errorInfo: ErrorInfo): void {
    // Log error for debugging
    console.error('Error boundary caught:', error, errorInfo);

    // Call optional error handler (for error tracking services)
    if (this.props.onError) {
      this.props.onError(error, errorInfo);
    }
  }

  componentDidUpdate(prevProps: Props): void {
    // React never retries a boundary on its own: once caught, the fallback
    // renders until something changes the state back. A page-scoped boundary
    // therefore has to be told when the user has moved on, or the fallback
    // outlives the page that threw and every page clicked next looks broken.
    if (this.state.hasError && prevProps.resetKey !== this.props.resetKey) {
      this.setState({ hasError: false, error: null });
    }
  }

  private handleReset = (): void => {
    this.setState({ hasError: false, error: null });
  };

  render(): ReactNode {
    if (this.state.hasError) {
      // Use custom fallback if provided
      if (this.props.fallback) {
        if (typeof this.props.fallback === 'function') {
          return this.props.fallback({ error: this.state.error, reset: this.handleReset });
        }
        return this.props.fallback;
      }

      // Default fallback UI
      return (
        <div className="min-h-screen flex items-center justify-center p-6">
          <div className="max-w-md text-center">
            <h1 className="text-4xl font-serif text-primary mb-4">
              Something went wrong
            </h1>
            <p className="text-primary/70 font-sans mb-2">
              {this.state.error?.message || 'An unexpected error occurred'}
            </p>
            <p className="text-primary/70 font-sans text-sm mb-6">
              This error has been logged. Try reloading the page.
            </p>
            <div className="flex gap-4 justify-center">
              <button
                type="button"
                onClick={this.handleReset}
                className="px-6 py-2 border border-primary/20 rounded-sm font-serif km-interactive km-focus-visible"
                aria-label="Try to recover from error"
              >
                Try Again
              </button>
              <button
                type="button"
                onClick={() => window.location.reload()}
                className="px-6 py-2 bg-primary text-bg-primary rounded-sm font-serif transition-opacity hover:opacity-90 cursor-pointer km-focus-visible"
                aria-label="Reload the page"
              >
                Reload Page
              </button>
            </div>
          </div>
        </div>
      );
    }

    return this.props.children;
  }
}
