import { createContext, useContext, useEffect, useLayoutEffect, useState, type ReactNode } from 'react';

const THEMES = ['night', 'day'] as const;
type Theme = (typeof THEMES)[number];

function isTheme(value: unknown): value is Theme {
    return typeof value === 'string' && (THEMES as readonly string[]).includes(value);
}

interface ThemeContextType {
    theme: Theme;
    toggleTheme: () => void;
}

const STORAGE_KEY = 'knightmind:theme';
const DARK_MQ = '(prefers-color-scheme: dark)';

const ThemeContext = createContext<ThemeContextType | undefined>(undefined);

function getSystemPreference(): Theme {
    if (typeof window === 'undefined') return 'night';
    return window.matchMedia(DARK_MQ).matches ? 'night' : 'day';
}

function getInitialTheme(): Theme {
    if (typeof window === 'undefined') return 'night';
    try {
        const stored = window.localStorage.getItem(STORAGE_KEY);
        if (isTheme(stored)) return stored;
    } catch {
        // localStorage unavailable
    }
    return getSystemPreference();
}

export function ThemeProvider({ children }: { children: ReactNode }) {
    const [theme, setTheme] = useState<Theme>(getInitialTheme);

    // Apply theme class to body synchronously before the browser paints
    // to avoid a flash of the wrong theme (FOUC).
    useLayoutEffect(() => {
        document.body.classList.remove('night', 'day');
        document.body.classList.add(theme);
    }, [theme]);

    // Listen for OS-level preference changes when the user hasn't stored a preference
    useEffect(() => {
        const mq = window.matchMedia(DARK_MQ);
        const handler = (e: MediaQueryListEvent) => {
            const hasStoredPref = window.localStorage.getItem(STORAGE_KEY) !== null;
            if (!hasStoredPref) {
                setTheme(e.matches ? 'night' : 'day');
            }
        };
        mq.addEventListener('change', handler);
        return () => mq.removeEventListener('change', handler);
    }, []);

    const toggleTheme = () => {
        setTheme((prev) => {
            const next: Theme = prev === 'night' ? 'day' : 'night';
            try {
                window.localStorage.setItem(STORAGE_KEY, next);
            } catch {
                // localStorage unavailable
            }
            return next;
        });
    };

    return (
        <ThemeContext.Provider value={{ theme, toggleTheme }}>
            {children}
        </ThemeContext.Provider>
    );
}

// eslint-disable-next-line react-refresh/only-export-components
export function useTheme() {
    const context = useContext(ThemeContext);
    if (context === undefined) {
        throw new Error('useTheme must be used within ThemeProvider');
    }
    return context;
}
