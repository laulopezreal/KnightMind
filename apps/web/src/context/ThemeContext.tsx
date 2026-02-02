import { createContext, useContext, useEffect, useState, type ReactNode } from 'react';

type Theme = 'night' | 'day';

interface ThemeContextType {
    theme: Theme;
    toggleTheme: () => void;
}

const STORAGE_KEY = 'knightmind:theme';

const ThemeContext = createContext<ThemeContextType | undefined>(undefined);

function getSystemPreference(): Theme {
    if (typeof window === 'undefined') return 'night';
    return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'night' : 'day';
}

function getInitialTheme(): Theme {
    if (typeof window === 'undefined') return 'night';
    try {
        const stored = window.localStorage.getItem(STORAGE_KEY);
        if (stored === 'night' || stored === 'day') return stored;
    } catch {
        // localStorage unavailable
    }
    return getSystemPreference();
}

export function ThemeProvider({ children }: { children: ReactNode }) {
    const [theme, setTheme] = useState<Theme>(getInitialTheme);

    // Apply theme class to body whenever it changes
    useEffect(() => {
        document.body.className = theme;
    }, [theme]);

    // Listen for OS-level preference changes when the user hasn't stored a preference
    useEffect(() => {
        const mq = window.matchMedia('(prefers-color-scheme: dark)');
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
            const next = prev === 'night' ? 'day' : 'night';
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
