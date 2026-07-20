import { Navigate, Outlet, useLocation } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';
import { DataStateLoading } from './DataState';

// Optional, build-time proactive route gate. Default OFF: the backend is the
// source of truth (a protected call 401s → the global handler routes to /login),
// so the SPA does not hard-require login and behaves exactly as today.
//
// Set VITE_REQUIRE_AUTH=true to also gate routes client-side — useful once the
// operator has flipped KNIGHTMIND_REQUIRE_AUTH on and wants the login page shown
// up front instead of after the first 401.
const REQUIRE_AUTH = import.meta.env.VITE_REQUIRE_AUTH === 'true';

export function RequireAuth() {
    const location = useLocation();
    const { isAuthenticated, isLoading } = useAuth();

    if (!REQUIRE_AUTH) {
        return <Outlet />;
    }

    // Still validating a stored token — don't flash the login page.
    if (isLoading) {
        return <DataStateLoading label="Loading…" />;
    }

    if (!isAuthenticated) {
        const from = location.pathname + location.search;
        return <Navigate to="/login" state={{ from }} replace />;
    }

    return <Outlet />;
}
