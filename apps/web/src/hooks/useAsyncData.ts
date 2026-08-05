import { useCallback, useEffect, useRef, useState } from 'react';
import { useLatestRequest } from './useLatestRequest';

export interface AsyncData<T> {
    /** The last successfully loaded value, or null before the first success. */
    data: T | null;
    /** User-facing message from the most recent failure, or null. */
    error: string | null;
    /** True only while the FIRST load is in flight — drives the full-page spinner. */
    loading: boolean;
    /** True while a later load is in flight, with `data` still showing the old value. */
    refreshing: boolean;
    /** Re-run the fetch (window focus, a mutation the page just made, a retry button). */
    reload: () => void;
}

export interface UseAsyncDataOptions {
    /**
     * When false, no fetch runs and the hook settles to `loading: false`. Use for
     * "no username yet" — every account-dependent page has that state, and
     * spelling it as `enabled` keeps the early return out of the caller.
     */
    enabled?: boolean;
    /** Fallback message when the thrown value is not an Error. */
    errorMessage?: string;
}

/**
 * Load data for a page, with the loading/error/staleness bookkeeping done once.
 *
 * Thirteen pages hand-rolled this: a data state, a `loading` flag, an `error`
 * string, a ref to tell first load from background refresh, and three
 * `token.isStale()` guards. The race guard (`useLatestRequest`) had been adopted
 * by five of them, so the other eight either re-derived it or raced -- a slow
 * response for the previous username could resolve last and overwrite the new
 * one's data. That is the class of bug PR #286 found four of.
 *
 * Usage:
 *
 *   const { data, loading, error, reload } = useAsyncData(
 *       (signal) => getDashboard(username, { signal }),
 *       [username],
 *       { enabled: Boolean(username) },
 *   );
 *
 * The fetcher is read through a ref, so an inline arrow does not re-trigger the
 * effect -- `deps` alone decides when to refetch. This is deliberate: making the
 * fetcher itself a dependency would either force every caller to wrap it in
 * useCallback or spin forever, and the second failure mode is silent until it
 * saturates the API.
 */
export function useAsyncData<T>(
    fetcher: (signal: AbortSignal) => Promise<T>,
    deps: React.DependencyList,
    options: UseAsyncDataOptions = {},
): AsyncData<T> {
    const { enabled = true, errorMessage = 'Something went wrong. Please try again.' } = options;

    const [data, setData] = useState<T | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [loading, setLoading] = useState(enabled);
    const [refreshing, setRefreshing] = useState(false);

    const request = useLatestRequest();
    // Latest fetcher without making it a dependency (see the note above).
    const fetcherRef = useRef(fetcher);
    fetcherRef.current = fetcher;
    // Distinguishes the first load from every later one, so a background refresh
    // does not blank the page it is refreshing.
    const hasLoadedRef = useRef(false);
    // Reload has to change identity to retrigger the effect; a counter is the
    // smallest thing that does.
    const [reloadCount, setReloadCount] = useState(0);
    const reload = useCallback(() => setReloadCount((n) => n + 1), []);

    useEffect(() => {
        if (!enabled) {
            // Settle rather than hang: a page with no username should render its
            // empty state, not a spinner forever.
            setLoading(false);
            setRefreshing(false);
            return;
        }

        const token = request.begin();
        const firstLoad = !hasLoadedRef.current;
        if (firstLoad) setLoading(true);
        else setRefreshing(true);
        setError(null);

        (async () => {
            try {
                const result = await fetcherRef.current(token.signal);
                // Every state write past an await is guarded: without this, the
                // superseded request wins whenever it happens to resolve last.
                if (token.isStale()) return;
                setData(result);
                hasLoadedRef.current = true;
            } catch (err) {
                if (token.isStale()) return;
                setError(err instanceof Error ? err.message : errorMessage);
            } finally {
                if (!token.isStale()) {
                    setLoading(false);
                    setRefreshing(false);
                }
            }
        })();
        // `deps` is the caller's contract for when to refetch; `fetcher` is
        // deliberately absent (read through a ref instead).
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [...deps, enabled, reloadCount, request, errorMessage]);

    return { data, error, loading, refreshing, reload };
}
