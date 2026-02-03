import '@testing-library/jest-dom';

// Provide a minimal localStorage polyfill for jsdom environments where
// window.localStorage may not be fully functional (jsdom v28+).
if (typeof window !== 'undefined' && (!window.localStorage || typeof window.localStorage.getItem !== 'function')) {
    const store: Record<string, string> = {};
    Object.defineProperty(window, 'localStorage', {
        value: {
            getItem: (key: string) => store[key] ?? null,
            setItem: (key: string, value: string) => { store[key] = String(value); },
            removeItem: (key: string) => { delete store[key]; },
            clear: () => { for (const key in store) { delete store[key]; } },
            get length() { return Object.keys(store).length; },
            key: (i: number) => Object.keys(store)[i] ?? null,
        },
        writable: true,
    });
}