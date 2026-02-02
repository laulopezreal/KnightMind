import { useState, useCallback } from 'react';

/**
 * Custom hook for persisting state to localStorage
 * @param key - localStorage key
 * @param initialValue - initial value if no stored value exists
 * @param parser - optional parser function for stored values (e.g., parseInt for numbers)
 * @returns [storedValue, setValue] tuple
 */
export function useLocalStorage<T>(
    key: string,
    initialValue: T,
    parser?: (value: string) => T
): [T, (value: T) => void] {
    const [storedValue, setStoredValue] = useState<T>(() => {
        if (typeof window === 'undefined') {
            return initialValue;
        }
        try {
            const item = window.localStorage.getItem(key);
            if (!item) return initialValue;

            // Use custom parser if provided, otherwise parse JSON
            return parser ? parser(item) : JSON.parse(item);
        } catch (error) {
            console.error(`Error reading localStorage key "${key}":`, error);
            return initialValue;
        }
    });

    const setValue = useCallback((value: T) => {
        try {
            setStoredValue(value);
            if (typeof window !== 'undefined') {
                window.localStorage.setItem(key, JSON.stringify(value));
            }
        } catch (error) {
            console.error(`Error setting localStorage key "${key}":`, error);
        }
    }, [key]);

    return [storedValue, setValue];
}
