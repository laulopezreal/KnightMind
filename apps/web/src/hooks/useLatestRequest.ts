import { useCallback, useEffect, useMemo, useRef } from 'react';

export interface RequestToken {
    /** Abort signal for the underlying fetch; aborted when a newer request starts. */
    signal: AbortSignal;
    /** True once a newer request has begun — the caller should drop this result. */
    isStale: () => boolean;
}

/**
 * Guards against stale-response races (e.g. the username changes mid-flight and
 * an older, slower response resolves after the newer one, clobbering the UI with
 * data for the wrong user).
 *
 * Each `begin()` bumps a generation counter and aborts the previous in-flight
 * request. The returned token's `isStale()` reports whether a newer request has
 * since started, so both the success and error paths can bail out:
 *
 *   const request = useLatestRequest();
 *   const token = request.begin();
 *   try {
 *     const data = await fetchThing(id, { signal: token.signal });
 *     if (token.isStale()) return;      // a newer fetch superseded this one
 *     setData(data);
 *   } catch (err) {
 *     if (token.isStale()) return;      // ignore the superseded request's error
 *     setError(err);
 *   }
 *
 * The generation check works even when the fetch layer ignores `signal`, so it's
 * safe to adopt incrementally.
 */
export function useLatestRequest(): { begin: () => RequestToken } {
    const generationRef = useRef(0);
    const controllerRef = useRef<AbortController | null>(null);

    useEffect(() => () => controllerRef.current?.abort(), []);

    const begin = useCallback((): RequestToken => {
        controllerRef.current?.abort();
        const controller = new AbortController();
        controllerRef.current = controller;
        const generation = ++generationRef.current;
        return {
            signal: controller.signal,
            isStale: () => generation !== generationRef.current,
        };
    }, []);

    // Stable identity so consumers can safely list the returned object in
    // useCallback/useEffect deps without re-triggering every render.
    return useMemo(() => ({ begin }), [begin]);
}
