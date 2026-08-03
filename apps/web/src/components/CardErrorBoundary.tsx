import type { ReactNode } from 'react';
import { ErrorBoundary } from './ErrorBoundary';
import { ErrorDetails } from './ErrorDetails';

interface CardErrorBoundaryProps {
  /** Name of the tile, e.g. "Rating change" — used in the fallback and its labels. */
  label: string;
  children: ReactNode;
}

/**
 * Tile-scoped containment, one level finer than {@link RouteErrorBoundary}.
 *
 * The Dashboard is a grid of independently-sourced tiles: the strip loader uses
 * `Promise.allSettled`, so a slice whose *fetch* fails simply omits its tile and
 * the rest of the page is unaffected. A tile whose *render* throws had no such
 * treatment — it cost the whole page, which is a strange asymmetry for the same
 * failure viewed from two angles. This closes it.
 *
 * `role="status"`, not `alert`: one dead tile among eight is informational and
 * non-blocking, the same call `DataStatePartial` already makes for partial data.
 * The fallback keeps the standard card shell so the grid does not reflow around
 * the gap.
 *
 * Retry is the boundary's own `reset`. React unmounts the subtree when a
 * boundary catches, so that remounts the tile and re-runs its effects — a real
 * second attempt, not a repaint.
 */
export function CardErrorBoundary({ label, children }: CardErrorBoundaryProps) {
  return (
    <ErrorBoundary
      fallback={({ error, reset }) => (
        <section
          className="bg-primary/5 border border-primary/10 rounded-sm p-6 backdrop-blur-sm h-full"
          role="status"
          aria-live="polite"
        >
          <h3 className="font-serif text-lg text-primary mb-2">{label}</h3>
          <p className="text-primary/70 font-sans text-sm mb-4">
            <span aria-hidden="true">⚠ </span>
            This card couldn&rsquo;t be displayed. The rest of the page is unaffected.
          </p>
          <button
            type="button"
            onClick={reset}
            className="px-4 py-1.5 border border-primary/20 text-primary rounded-sm font-serif text-sm km-interactive km-focus-visible"
            aria-label={`Try loading ${label} again`}
          >
            Try again
          </button>
          <ErrorDetails error={error} />
        </section>
      )}
    >
      {children}
    </ErrorBoundary>
  );
}
