import type { ReactNode } from 'react';

interface DataStateLoadingProps {
  label: string;
  compact?: boolean;
}

export function DataStateLoading({ label, compact = false }: DataStateLoadingProps) {
  if (compact) {
    return (
      <div className="flex items-center gap-2 text-primary/70 font-sans text-xs" role="status" aria-live="polite">
        <div className="animate-spin h-4 w-4 border-2 border-primary/20 border-t-primary rounded-full" aria-hidden="true" />
        <span>{label}</span>
      </div>
    );
  }

  return (
    <div className="flex items-center justify-center min-h-[40vh]" role="status" aria-live="polite">
      <div className="animate-spin h-12 w-12 border-4 border-primary/20 border-t-primary rounded-full" aria-hidden="true" />
      <span className="sr-only">{label}</span>
    </div>
  );
}

interface DataStateSkeletonProps {
  /** Screen-reader announcement, e.g. "Analyzing games..." — also findable by tests via getByText. */
  label: string;
  /** Layout classes for the block container, e.g. "space-y-12 animate-teedin". */
  className?: string;
  /** Pulse blocks (`animate-pulse` divs) mirroring the loaded layout. */
  children: ReactNode;
}

/**
 * Layout-mirroring loading skeleton. Callers supply pulse blocks that
 * approximate the loaded layout; the wrapper provides the `role="status"` +
 * sr-only announcement so every skeleton reads as "loading" to assistive
 * tech instead of a silent page of empty boxes.
 */
export function DataStateSkeleton({ label, className, children }: DataStateSkeletonProps) {
  return (
    <div role="status" aria-live="polite">
      <span className="sr-only">{label}</span>
      <div aria-hidden="true" className={className}>
        {children}
      </div>
    </div>
  );
}

interface DataStateErrorProps {
  message: string;
  onRetry: () => void;
  retryLabel: string;
  ariaLabel: string;
  compact?: boolean;
}

export function DataStateError({ message, onRetry, retryLabel, ariaLabel, compact = false }: DataStateErrorProps) {
  return (
    <div className={`${compact ? "text-left p-4" : "max-w-md mx-auto mt-24 text-center p-8"} bg-red-500/5 border border-red-500/20 rounded-sm`} role="alert" aria-live="assertive">
      {/* break-words: the message can be server text, and one long unbroken
          token (a URL in an exception string) otherwise paints outside this
          panel and makes the whole page scroll sideways on a phone. */}
      <p className="text-negative mb-4 break-words">{message}</p>
      <button
        type="button"
        onClick={onRetry}
        className="px-6 py-2 border border-primary/20 rounded-sm km-interactive km-focus-visible"
        aria-label={ariaLabel}
      >
        {retryLabel}
      </button>
    </div>
  );
}

interface DataStateEmptyProps {
  title: string;
  description: string;
  actionLabel: string;
  onAction: () => void;
}

/**
 * Empty/first-run panel: a title, a line of explanation, and the one action
 * that fills the state in.
 *
 * The title is an `<h2>` because it *is* the heading for whatever the page is
 * showing instead of its content — every call site sits directly beneath the
 * `<h1>` from `PageHeader`, so h2 is the right level and no page skips one.
 * Styled as an h2 it would jump to Cormorant 500, so `km-heading-sans` keeps
 * the body face; see the note on that class in `index.css`.
 */
export function DataStateEmpty({ title, description, actionLabel, onAction }: DataStateEmptyProps) {
  return (
    <div className="bg-primary/5 border border-primary/10 rounded-sm p-10 text-center">
      <h2 className="text-primary/70 km-heading-sans text-lg mb-3">{title}</h2>
      <p className="text-primary/70 font-sans text-sm mb-6">{description}</p>
      <button
        type="button"
        onClick={onAction}
        className="px-6 py-2 bg-primary text-bg-primary rounded-sm font-serif transition-opacity hover:opacity-90 cursor-pointer km-focus-visible"
      >
        {actionLabel}
      </button>
    </div>
  );
}

interface DataStatePartialProps {
  /** What loaded successfully — shown as the panel's children. */
  children: ReactNode;
  /** Short line naming what failed to load, e.g. "Tactical insights are unavailable." */
  message: string;
  onRetry?: () => void;
  retryLabel?: string;
  /** Disables the retry button (e.g. while a retry is already in flight). */
  retryPending?: boolean;
}

/**
 * Partial-success: some data rendered, but a secondary part failed. The banner
 * is a non-blocking `role="status"` (the primary content is still usable) and
 * uses an amber warning cue plus a "Partial data" text label — never colour
 * alone — so it reads for colour-blind and screen-reader users alike.
 */
export function DataStatePartial({
  children,
  message,
  onRetry,
  retryLabel = 'Retry',
  retryPending = false,
}: DataStatePartialProps) {
  return (
    <div className="space-y-4">
      <div
        className="flex flex-wrap items-center gap-x-3 gap-y-2 bg-amber-500/5 border border-amber-500/20 rounded-sm p-4"
        role="status"
        aria-live="polite"
      >
        <span className="text-warning font-sans text-sm font-medium" aria-hidden="true">
          ⚠ Partial data
        </span>
        <p className="text-primary/70 font-sans text-sm flex-1 min-w-[12rem]">{message}</p>
        {onRetry && (
          <button
            type="button"
            onClick={onRetry}
            disabled={retryPending}
            className={`px-4 py-1.5 border border-primary/20 text-primary rounded-sm font-serif text-sm transition-all km-focus-visible ${retryPending ? 'km-interactive-disabled disabled:opacity-50' : 'km-interactive'}`}
          >
            {retryPending ? `${retryLabel}…` : retryLabel}
          </button>
        )}
      </div>
      {children}
    </div>
  );
}

interface DataStateStaleProps {
  children: ReactNode;
  /** e.g. "Showing your last saved data" or "Updated 5 minutes ago". */
  message: string;
  onRefresh?: () => void;
  refreshLabel?: string;
  refreshPending?: boolean;
}

/**
 * Stale-data: cached/last-known content is shown while a refresh failed or is
 * pending. Marked `role="status"` (informational, non-blocking) with a text
 * "Showing older data" cue in addition to the icon, so the staleness is not
 * conveyed by colour alone.
 */
export function DataStateStale({
  children,
  message,
  onRefresh,
  refreshLabel = 'Refresh',
  refreshPending = false,
}: DataStateStaleProps) {
  return (
    <div className="space-y-4">
      <div
        className="flex flex-wrap items-center gap-x-3 gap-y-2 bg-primary/5 border border-primary/20 rounded-sm p-4"
        role="status"
        aria-live="polite"
      >
        <span className="text-primary/70 font-sans text-sm font-medium" aria-hidden="true">
          ↻ Showing older data
        </span>
        <p className="text-primary/70 font-sans text-sm flex-1 min-w-[12rem]">{message}</p>
        {onRefresh && (
          <button
            type="button"
            onClick={onRefresh}
            disabled={refreshPending}
            className={`px-4 py-1.5 border border-primary/20 text-primary rounded-sm font-serif text-sm transition-all km-focus-visible ${refreshPending ? 'km-interactive-disabled disabled:opacity-50' : 'km-interactive'}`}
          >
            {refreshPending ? `${refreshLabel}…` : refreshLabel}
          </button>
        )}
      </div>
      {children}
    </div>
  );
}

interface DataStateOfflineProps {
  /** Optional override copy; a sensible default is provided. */
  message?: string;
  onRetry?: () => void;
  retryLabel?: string;
  compact?: boolean;
}

/**
 * Offline affordance. Pair with `useOnlineStatus()`: render this when a fetch
 * failed *and* the browser reports offline, so the user gets "you're offline"
 * instead of a misleading "server error". Uses `role="alert"` because losing
 * connectivity is an actionable interruption, and a text label ("Offline") in
 * addition to the icon.
 */
export function DataStateOffline({
  message = "You appear to be offline. Check your connection and try again.",
  onRetry,
  retryLabel = 'Retry',
  compact = false,
}: DataStateOfflineProps) {
  return (
    <div
      className={`${compact ? 'text-left p-4' : 'max-w-md mx-auto mt-24 text-center p-8'} bg-amber-500/5 border border-amber-500/20 rounded-sm`}
      role="alert"
      aria-live="assertive"
    >
      <p className="text-warning font-sans text-sm font-medium mb-2" aria-hidden="true">
        ⚠ Offline
      </p>
      <p className="text-primary/70 font-sans mb-4">{message}</p>
      {onRetry && (
        <button
          type="button"
          onClick={onRetry}
          className="px-6 py-2 border border-primary/20 text-primary rounded-sm km-interactive km-focus-visible font-serif"
        >
          {retryLabel}
        </button>
      )}
    </div>
  );
}
