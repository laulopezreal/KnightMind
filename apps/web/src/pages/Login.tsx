import { useEffect, useState, type FormEvent } from 'react';
import { Navigate, useLocation, useNavigate } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';
import { ApiError } from '../api/core';

interface LocationState {
    from?: string;
}

export default function Login() {
    const { login, isAuthenticated } = useAuth();
    const navigate = useNavigate();
    const location = useLocation();

    const [email, setEmail] = useState('');
    const [password, setPassword] = useState('');
    const [submitting, setSubmitting] = useState(false);
    const [error, setError] = useState<string | null>(null);

    // Where to return after a successful login: the page a 401 bounced us from,
    // falling back to Home.
    const from = (location.state as LocationState | null)?.from ?? '/';

    // Already signed in? Don't show the form — go where they were headed.
    useEffect(() => {
        if (isAuthenticated) {
            navigate(from, { replace: true });
        }
    }, [isAuthenticated, from, navigate]);

    if (isAuthenticated) {
        return <Navigate to={from} replace />;
    }

    const handleSubmit = async (e: FormEvent) => {
        e.preventDefault();
        if (submitting) return;

        const trimmedEmail = email.trim();
        if (!trimmedEmail || !password) {
            setError('Enter your email and password.');
            return;
        }

        setSubmitting(true);
        setError(null);
        try {
            await login(trimmedEmail, password);
            navigate(from, { replace: true });
        } catch (err) {
            if (err instanceof ApiError) {
                if (err.detail) console.error('[login]', err.detail);
                // 401 → "Invalid email or password" from the backend; other
                // statuses carry their own friendly message from the API layer.
                setError(err.statusCode === 401 ? 'Invalid email or password.' : err.message);
            } else {
                setError('Could not sign in. Please try again.');
            }
        } finally {
            setSubmitting(false);
        }
    };

    return (
        <div className="max-w-[420px] mx-auto space-y-8 animate-teedin">
            <header className="space-y-2">
                <h1 className="text-4xl font-serif font-medium text-primary">Sign in</h1>
                <p className="font-sans text-primary/60">
                    Access your KnightMind training account.
                </p>
            </header>

            <form onSubmit={handleSubmit} className="space-y-6" noValidate>
                <div className="space-y-2">
                    <label
                        htmlFor="login-email"
                        className="block text-xs font-sans uppercase tracking-widest text-primary/60"
                    >
                        Email
                    </label>
                    <input
                        id="login-email"
                        type="email"
                        name="email"
                        autoComplete="username"
                        autoFocus
                        value={email}
                        onChange={(e) => setEmail(e.target.value)}
                        disabled={submitting}
                        className="w-full bg-transparent border-b border-primary/20 py-2 text-primary placeholder-primary/30 focus:outline-none focus:border-primary/60 transition-colors font-serif text-xl disabled:opacity-50"
                    />
                </div>

                <div className="space-y-2">
                    <label
                        htmlFor="login-password"
                        className="block text-xs font-sans uppercase tracking-widest text-primary/60"
                    >
                        Password
                    </label>
                    <input
                        id="login-password"
                        type="password"
                        name="password"
                        autoComplete="current-password"
                        value={password}
                        onChange={(e) => setPassword(e.target.value)}
                        disabled={submitting}
                        className="w-full bg-transparent border-b border-primary/20 py-2 text-primary placeholder-primary/30 focus:outline-none focus:border-primary/60 transition-colors font-serif text-xl disabled:opacity-50"
                    />
                </div>

                {error && (
                    <p role="alert" className="text-negative font-sans text-sm">
                        {error}
                    </p>
                )}

                <button
                    type="submit"
                    disabled={submitting}
                    className="w-full px-6 py-3 bg-primary text-bg-primary hover:opacity-90 rounded-sm font-serif transition-colors disabled:opacity-50 km-focus-visible"
                >
                    {submitting ? 'Signing in…' : 'Sign in'}
                </button>
            </form>
        </div>
    );
}
