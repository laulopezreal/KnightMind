import { useEffect, useState } from 'react';

/**
 * Tracks the browser's connectivity using `navigator.onLine` plus the
 * `online` / `offline` window events.
 *
 * `navigator.onLine` only tells us whether the browser has a network route, not
 * whether our API is reachable — but a `false` value is a reliable signal that
 * any fetch will fail, so it's worth surfacing to the user before they retry
 * into the void. Components pair this with a data-fetch error to distinguish
 * "the server errored" from "you're offline".
 */
export function useOnlineStatus(): boolean {
    const [online, setOnline] = useState<boolean>(() =>
        typeof navigator === 'undefined' ? true : navigator.onLine,
    );

    useEffect(() => {
        const goOnline = () => setOnline(true);
        const goOffline = () => setOnline(false);
        window.addEventListener('online', goOnline);
        window.addEventListener('offline', goOffline);
        return () => {
            window.removeEventListener('online', goOnline);
            window.removeEventListener('offline', goOffline);
        };
    }, []);

    return online;
}
