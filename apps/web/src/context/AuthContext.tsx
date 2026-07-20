import {
    createContext,
    useCallback,
    useContext,
    useEffect,
    useState,
    type ReactNode,
} from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { ApiError, setAuthToken, setUnauthorizedHandler } from '../api/core';
import { login as apiLogin, me as apiMe, type Account } from '../api/auth';

// localStorage key holding the JWT bearer token. Namespaced like the app's other
// keys (knightmind:theme, knightmind:chesscom_username).
//
// Storage choice — localStorage vs httpOnly cookie: localStorage is readable by
// any script on the page, so a successful XSS could exfiltrate the token. An
// httpOnly cookie is not JS-readable and is the stronger option, but it needs
// backend cookie/CSRF support that the current API (bearer-token only) doesn't
// have. For a single-operator personal app this trade-off is acceptable; if the
// app ever gains a public signup surface, revisit and move to httpOnly cookies.
export const AUTH_TOKEN_KEY = 'knightmind:auth_token';

function readStoredToken(): string | null {
    try {
        return window.localStorage.getItem(AUTH_TOKEN_KEY);
    } catch {
        return null;
    }
}

interface AuthContextType {
    /** The current JWT, or null when logged out / auth is off. */
    token: string | null;
    /** The authenticated account, or null until validated / when logged out. */
    account: Account | null;
    /** True while an existing token is being validated against /auth/me on load. */
    isLoading: boolean;
    /** Whether a token is currently held (does not by itself prove validity). */
    isAuthenticated: boolean;
    /** Exchange credentials for a token, persist it, and load the account. */
    login: (email: string, password: string) => Promise<void>;
    /** Clear the token + account and return to the login page. */
    logout: () => void;
}

const AuthContext = createContext<AuthContextType | undefined>(undefined);

export function AuthProvider({ children }: { children: ReactNode }) {
    const navigate = useNavigate();
    const location = useLocation();

    // Seed from localStorage and push the token into the API layer synchronously
    // during the first render — before any child effect fires a request — so the
    // very first protected call already carries the Authorization header.
    const [token, setTokenState] = useState<string | null>(() => {
        const stored = readStoredToken();
        setAuthToken(stored);
        return stored;
    });
    const [account, setAccount] = useState<Account | null>(null);
    const [isLoading, setIsLoading] = useState<boolean>(() => readStoredToken() !== null);

    // Single source of truth for token state: keeps localStorage, the API layer,
    // and React state in lockstep.
    const persistToken = useCallback((next: string | null) => {
        try {
            if (next) {
                window.localStorage.setItem(AUTH_TOKEN_KEY, next);
            } else {
                window.localStorage.removeItem(AUTH_TOKEN_KEY);
            }
        } catch {
            // localStorage unavailable (private mode / disabled) — the in-memory
            // token still works for this session.
        }
        setAuthToken(next);
        setTokenState(next);
    }, []);

    const logout = useCallback(() => {
        persistToken(null);
        setAccount(null);
        navigate('/login');
    }, [persistToken, navigate]);

    const login = useCallback(
        async (email: string, password: string) => {
            const res = await apiLogin(email, password);
            persistToken(res.access_token);
            // Validate immediately and load the account; on failure roll back so we
            // don't hold a token that /auth/me rejects.
            try {
                const acct = await apiMe();
                setAccount(acct);
            } catch (err) {
                persistToken(null);
                setAccount(null);
                throw err;
            }
        },
        [persistToken],
    );

    // Register the global 401 handler. On an unauthorized response the API layer
    // calls this: clear the stale token and route to /login, remembering where the
    // user was so we can send them back after a successful login.
    useEffect(() => {
        setUnauthorizedHandler(() => {
            persistToken(null);
            setAccount(null);
            const from = location.pathname + location.search;
            navigate('/login', { state: { from }, replace: true });
        });
        return () => setUnauthorizedHandler(null);
    }, [persistToken, navigate, location.pathname, location.search]);

    // On mount, validate an existing token by loading the account. A 401 is
    // handled by the global handler above (clears token + redirects); other
    // errors (e.g. server down) leave the token in place so a transient outage
    // doesn't log the user out.
    useEffect(() => {
        // No stored token → isLoading was already seeded false by the initializer;
        // nothing to validate.
        if (readStoredToken() === null) {
            return;
        }
        let cancelled = false;
        apiMe()
            .then((acct) => {
                if (!cancelled) setAccount(acct);
            })
            .catch((err) => {
                // 401 already triggered logout via the unauthorized handler.
                if (!(err instanceof ApiError) || err.statusCode !== 401) {
                    // Non-auth failure: keep the token, just stop the loading state.
                }
            })
            .finally(() => {
                if (!cancelled) setIsLoading(false);
            });
        return () => {
            cancelled = true;
        };
        // Intentionally run once on mount (only module-level, stable references used).
    }, []);

    return (
        <AuthContext.Provider
            value={{
                token,
                account,
                isLoading,
                isAuthenticated: token !== null,
                login,
                logout,
            }}
        >
            {children}
        </AuthContext.Provider>
    );
}

// eslint-disable-next-line react-refresh/only-export-components
export function useAuth() {
    const context = useContext(AuthContext);
    if (context === undefined) {
        throw new Error('useAuth must be used within an AuthProvider');
    }
    return context;
}

// Non-throwing variant for chrome (e.g. the sidebar logout control) that may
// render in contexts/tests without an AuthProvider. Returns undefined there.
// eslint-disable-next-line react-refresh/only-export-components
export function useOptionalAuth(): AuthContextType | undefined {
    return useContext(AuthContext);
}
