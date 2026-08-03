import type { ReactNode } from 'react';
import { useLocation } from 'react-router-dom';
import { ErrorBoundary } from './ErrorBoundary';

interface RouteErrorBoundaryProps {
  children: ReactNode;
}

/**
 * Page-scoped error boundary, mounted inside Layout so a throw in one page
 * costs the content column and nothing else.
 *
 * The root boundary in App.tsx wraps the entire tree, so any render error —
 * a malformed API field reaching a component was the recurring one — replaced
 * the whole shell, sidebar and nav included, with a full-screen "Something went
 * wrong" whose only exit was Reload Page. Losing a page is a page-sized
 * problem; the user should still be able to walk to another one. The root
 * boundary stays as the last resort for the chrome itself failing.
 *
 * `resetKey` is the pathname because a boundary that has caught keeps showing
 * its fallback forever — without it the sidebar would navigate and every
 * destination would render the dead page's error.
 */
export function RouteErrorBoundary({ children }: RouteErrorBoundaryProps) {
  const { pathname } = useLocation();

  return (
    <ErrorBoundary
      resetKey={pathname}
      fallback={({ error, reset }) => (
        <div
          className="max-w-md mx-auto mt-24 text-center p-8 bg-red-500/5 border border-red-500/20 rounded-sm"
          role="alert"
          aria-live="assertive"
        >
          <h2 className="text-2xl font-serif text-primary mb-3">This page didn’t load</h2>
          <p className="text-negative font-sans text-sm mb-2">
            {error?.message || 'An unexpected error occurred'}
          </p>
          <p className="text-primary/70 font-sans text-sm mb-6">
            The rest of KnightMind still works — pick another page from the menu, or try again.
          </p>
          <div className="flex flex-wrap gap-3 justify-center">
            <button
              type="button"
              onClick={reset}
              className="px-6 py-2 border border-primary/20 rounded-sm font-serif km-interactive km-focus-visible"
              aria-label="Try loading this page again"
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
      )}
    >
      {children}
    </ErrorBoundary>
  );
}
