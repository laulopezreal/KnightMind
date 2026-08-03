import { useEffect, useRef, type ReactNode } from 'react';
import { useLocation } from 'react-router-dom';
import { ErrorBoundary } from './ErrorBoundary';

const ISSUES_URL = 'https://github.com/laulopezreal/KnightMind/issues';

interface RouteErrorBoundaryProps {
  children: ReactNode;
}

interface RouteErrorFallbackProps {
  error: Error | null;
  reset: () => void;
  pathname: string;
}

/**
 * Prefilled issue for the page that just failed. A page-scoped boundary makes
 * failures quiet — the app still looks fine, so nothing pushes the user to
 * report and the underlying bug survives. There is no error-tracking service
 * in this stack, so the user is the reporting channel; the least we can do is
 * carry the route and the message for them.
 */
function reportUrl(error: Error | null, pathname: string): string {
  const params = new URLSearchParams({
    title: `Page failed to render: ${pathname}`,
    body: [
      `**Route:** \`${pathname}\``,
      `**Error:** \`${error?.message || 'unknown'}\``,
      '',
      'What were you doing when this happened?',
    ].join('\n'),
  });
  return `${ISSUES_URL}/new?${params.toString()}`;
}

function RouteErrorFallback({ error, reset, pathname }: RouteErrorFallbackProps) {
  const alertRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    // Focus is on whatever the user just activated — and when Try Again leads
    // to another throw, this fallback remounts and focus falls to <body>,
    // dropping a keyboard user at the top of the document with no idea why.
    // Move it onto the alert, which is also where the recovery controls are.
    alertRef.current?.focus();
  }, []);

  return (
    <div
      ref={alertRef}
      tabIndex={-1}
      className="max-w-md mx-auto mt-24 text-center p-8 bg-red-500/5 border border-red-500/20 rounded-sm"
      role="alert"
      aria-live="assertive"
    >
      {/* h1: the page's own heading is gone, and Layout has none, so anything
          lower leaves the document with no top-level heading. */}
      <h1 className="text-2xl font-serif text-primary mb-3">This page didn’t load</h1>
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
      <a
        href={reportUrl(error, pathname)}
        target="_blank"
        rel="noopener noreferrer"
        className="inline-block mt-5 text-primary/70 font-sans text-xs underline underline-offset-4 km-interactive km-focus-visible"
      >
        Report this
      </a>
    </div>
  );
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
 * `resetKey` is the location key, not the pathname: a boundary that has caught
 * keeps showing its fallback forever, so without it the sidebar would navigate
 * and every destination would render the dead page's error. The key changes on
 * every navigation including a re-click of the current page's own link, so that
 * link retries instead of looking dead.
 */
export function RouteErrorBoundary({ children }: RouteErrorBoundaryProps) {
  const { key, pathname } = useLocation();

  return (
    <ErrorBoundary
      resetKey={key}
      onError={(error) => {
        // componentDidCatch already logs; this adds the route, without which a
        // console line from a lazily-loaded chunk says little about where it
        // came from.
        console.error(`[route error] ${pathname}:`, error.message);
      }}
      fallback={({ error, reset }) => (
        <RouteErrorFallback error={error} reset={reset} pathname={pathname} />
      )}
    >
      {children}
    </ErrorBoundary>
  );
}
