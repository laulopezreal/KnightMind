import { useOptionalAuth } from '../context/AuthContext';

// Logout control for the sidebar footer. Uses the non-throwing auth hook so it
// renders nothing when there's no AuthProvider (e.g. isolated Sidebar tests) and
// nothing when logged out — keeping the app's flag-off appearance unchanged.
export default function AuthControl() {
    const auth = useOptionalAuth();

    if (!auth || !auth.isAuthenticated) {
        return null;
    }

    return (
        <div className="font-sans space-y-1">
            {auth.account?.email && (
                <p className="text-xs text-primary/60 truncate" title={auth.account.email}>
                    {auth.account.email}
                </p>
            )}
            <button
                type="button"
                onClick={auth.logout}
                className="text-sm text-primary/70 hover:text-primary km-interactive km-focus-visible rounded-sm px-1 -mx-1"
            >
                Log out
            </button>
        </div>
    );
}
