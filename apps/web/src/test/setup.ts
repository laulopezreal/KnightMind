import '@testing-library/jest-dom';
import { configure } from '@testing-library/react';

// Testing Library's 1000ms default for `waitFor`/`find*` is a budget, not a
// deadline: a passing assertion still resolves as soon as it can, so raising it
// costs nothing on the happy path and only delays reporting a genuine failure.
//
// Measured, the assertions in this suite settle in tens of milliseconds — but a
// contended machine (the whole suite in one process, or a parallel backend run,
// or a shared CI runner) has been observed pushing one past 1000ms and failing
// it on timing alone. Three seconds keeps that headroom without hiding real
// hangs, which blow any budget.
configure({ asyncUtilTimeout: 3000 });

// jsdom ships no ResizeObserver. Components that refit on container resize
// (e.g. OpeningGraph) construct one at mount, so provide an inert stub —
// jsdom never lays out, so a resize would never fire anyway.
if (typeof globalThis.ResizeObserver === 'undefined') {
    globalThis.ResizeObserver = class {
        observe() { }
        unobserve() { }
        disconnect() { }
    } as unknown as typeof ResizeObserver;
}

// Provide a minimal localStorage polyfill for jsdom environments where
// window.localStorage may not be fully functional (jsdom v28+).
if (typeof window !== 'undefined' && (!window.localStorage || typeof window.localStorage.getItem !== 'function')) {
    const store: Record<string, string> = {};
    Object.defineProperty(window, 'localStorage', {
        value: {
            getItem: (key: string) => store[key] ?? null,
            setItem: (key: string, value: string) => { store[key] = value; },
            removeItem: (key: string) => { delete store[key]; },
            clear: () => { for (const key in store) { delete store[key]; } },
            get length() { return Object.keys(store).length; },
            key: (i: number) => Object.keys(store)[i] ?? null,
        },
        writable: true,
    });
}