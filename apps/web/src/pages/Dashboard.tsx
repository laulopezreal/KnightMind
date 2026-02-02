import { useState, useEffect, useCallback, useRef } from 'react';
import { useNavigate, Link } from 'react-router-dom';
import { getDashboardSummary, getMotifPerformance, getMotifTrends, type DashboardSummary, type MotifPerformanceResponse, type TrendsResponse } from '../api/users';
import { getRecentSessions, type SessionSummary } from '../api/sessions';
import { useChessUsername } from '../context/ChessUsernameContext';
import { TacticalRadar } from '../components/TacticalRadar';
import { MotifTrends } from '../components/MotifTrends';
import { RecentSessionsCard } from '../components/RecentSessionsCard';
import { formatRelativeTime } from '../utils/time';

export default function Dashboard() {
    const { username } = useChessUsername();
    const navigate = useNavigate();

    const [dashboardData, setDashboardData] = useState<DashboardSummary | null>(null);
    const [motifPerformance, setMotifPerformance] = useState<MotifPerformanceResponse | null>(null);
    const [recentSessions, setRecentSessions] = useState<SessionSummary[]>([]);
    const [trends, setTrends] = useState<TrendsResponse | null>(null);
    const [loading, setLoading] = useState(true);
    const [refreshing, setRefreshing] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const isFetchingRef = useRef(false);
    const isMountedRef = useRef(true);

    // Track mounted status to prevent state updates after unmount
    useEffect(() => {
        isMountedRef.current = true;
        return () => {
            isMountedRef.current = false;
        };
    }, []);

    // Redirect if no username
    useEffect(() => {
        if (!username) {
            navigate('/');
        }
    }, [username, navigate]);

    // Load all dashboard data - extracted for reusability
    const loadDashboardData = useCallback(async (isRefresh = false) => {
        // Guard against concurrent fetches
        if (!username || isFetchingRef.current) return;

        isFetchingRef.current = true;
        try {
            if (isRefresh) {
                setRefreshing(true);
            } else {
                setLoading(true);
            }
            setError(null);

            const [dashboard, motifs, sessions, trendsData] = await Promise.all([
                getDashboardSummary(username),
                getMotifPerformance(username),
                getRecentSessions(username, 5),
                getMotifTrends(username, 30)
            ]);

            if (isMountedRef.current) {
                setDashboardData(dashboard);
                setMotifPerformance(motifs);
                setRecentSessions(sessions);
                setTrends(trendsData);
            }
        } catch (err) {
            console.error('Failed to load dashboard:', err);
            if (isMountedRef.current) {
                setError(err instanceof Error ? err.message : 'Failed to load dashboard data');
            }
        } finally {
            if (isMountedRef.current) {
                setLoading(false);
                setRefreshing(false);
            }
            isFetchingRef.current = false;
        }
    }, [username]);

    // Initial load
    useEffect(() => {
        loadDashboardData();
    }, [loadDashboardData]);

    // Auto-refresh on window focus
    useEffect(() => {
        const handleFocus = () => {
            loadDashboardData(true);
        };

        window.addEventListener('focus', handleFocus);
        return () => {
            window.removeEventListener('focus', handleFocus);
        };
    }, [loadDashboardData]);

    const handleMotifClick = (motif: string) => {
        // Navigate to puzzles page with motif filter
        navigate(`/puzzles?motif=${encodeURIComponent(motif)}`);
    };

    if (loading) {
        return (
            <div className="flex items-center justify-center min-h-screen" role="status" aria-live="polite">
                <div className="animate-spin h-12 w-12 border-4 border-primary/20 border-t-primary rounded-full" aria-hidden="true" />
                <span className="sr-only">Loading dashboard...</span>
            </div>
        );
    }

    if (error || !dashboardData) {
        return (
            <div className="max-w-md mx-auto mt-32 text-center" role="alert" aria-live="assertive">
                <p className="text-red-500 mb-4">
                    {error || 'Failed to load dashboard data'}
                </p>
                <button
                    type="button"
                    onClick={() => window.location.reload()}
                    className="px-6 py-2 border border-primary/20 rounded-sm km-interactive km-focus-visible"
                    aria-label="Retry loading dashboard"
                >
                    Retry
                </button>
            </div>
        );
    }

    return (
        <main className="container mx-auto p-6 space-y-8">
            {/* Header */}
            <section>
                <div className="flex justify-between items-end">
                    <div>
                        <h1 className="text-4xl md:text-5xl font-serif text-primary mb-2">
                            Dashboard
                        </h1>
                        <p className="text-lg text-primary/60 font-sans">
                            Your chess training overview
                        </p>
                    </div>
                    <div className="flex items-center gap-4">
                        <button
                            type="button"
                            onClick={() => loadDashboardData(true)}
                            disabled={refreshing}
                            title="Refresh dashboard data"
                            aria-label="Refresh dashboard data"
                            aria-busy={refreshing}
                            className={`px-4 py-2 border border-primary/20 rounded-sm font-sans text-sm transition-all km-focus-visible ${
                                refreshing ? 'km-interactive-disabled' : 'km-interactive'
                            }`}
                        >
                            {refreshing ? (
                                <>
                                    <span className="animate-spin inline-block h-4 w-4 border-2 border-primary/20 border-t-primary rounded-full mr-2" aria-hidden="true"></span>
                                    Refreshing...
                                </>
                            ) : (
                                <>
                                    <span className="inline-block mr-2" aria-hidden="true">↻</span>
                                    Refresh
                                </>
                            )}
                        </button>
                        <Link to="/" className="text-primary/60 hover:text-primary text-sm">
                            ← Home
                        </Link>
                    </div>
                </div>
            </section>

            {/* Warmup Prompt */}
            {dashboardData.needs_warmup && (
                <section className="bg-blue-500/10 border border-blue-500/20 rounded-sm p-6">
                    <h3 className="text-lg font-serif text-primary mb-2">
                        Welcome back! 🎯
                    </h3>
                    <p className="text-primary/60 mb-4 font-sans">
                        You've been away {dashboardData.days_since_last_session} days.
                        Let's do a quick warmup to see what stuck!
                    </p>
                    <button
                        type="button"
                        onClick={() => navigate('/puzzles?warmup=true')}
                        className="px-6 py-3 bg-primary text-bg-primary rounded-sm font-serif km-interactive km-focus-visible"
                    >
                        Start Warmup Diagnostic (5 puzzles)
                    </button>
                </section>
            )}

            {/* Tactical Radar */}
            {motifPerformance && motifPerformance.motifs.length > 0 && (
                <TacticalRadar motifs={motifPerformance.motifs} onMotifClick={handleMotifClick} />
            )}

            {/* Motif Trends */}
            {trends && trends.motif_trends.length > 0 && (
                <MotifTrends trends={trends.motif_trends} windowDays={trends.window_days} />
            )}

            {/* Stats Grid */}
            <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                {/* Recent Form */}
                <section className="bg-primary/5 border border-primary/10 rounded-sm p-6">
                    <h3 className="text-lg font-serif text-primary mb-4">
                        📊 Recent Form
                    </h3>

                    <div className="space-y-3">
                        {/* Visual streak */}
                        <div className="flex gap-1 flex-wrap">
                            {dashboardData.recent_form.last_20_results.map((result, i) => (
                                <div
                                    key={i}
                                    className={`w-6 h-6 rounded-sm ${
                                        result === 'pass'
                                            ? 'bg-green-500'
                                            : 'bg-red-500'
                                    }`}
                                    title={result === 'pass' ? 'Correct' : 'Incorrect'}
                                />
                            ))}
                        </div>

                        {/* Accuracy */}
                        <div className="flex justify-between items-center">
                            <span className="text-primary/60 font-sans">Last 20 puzzles</span>
                            <span className="text-2xl font-mono text-primary">
                                {Math.round(dashboardData.recent_form.accuracy * 100)}%
                            </span>
                        </div>

                        {/* Trend indicator */}
                        <div className="flex items-center gap-2">
                            <span className="text-primary/60 font-sans">Trend:</span>
                            {dashboardData.recent_form.trend === 'up' ? (
                                <span className="text-green-500">↗️ Improving</span>
                            ) : dashboardData.recent_form.trend === 'down' ? (
                                <span className="text-red-500">↘️ Declining</span>
                            ) : (
                                <span className="text-primary/60">→ Steady</span>
                            )}
                        </div>
                    </div>
                </section>

                {/* Training Schedule */}
                <section className="bg-primary/5 border border-primary/10 rounded-sm p-6">
                    <h3 className="text-lg font-serif text-primary mb-4">
                        ⏰ Training Schedule
                    </h3>

                    <div className="space-y-4">
                        <div>
                            <p className="text-3xl font-mono text-primary">
                                {dashboardData.schedule.due_now}
                            </p>
                            <p className="text-primary/60 text-sm font-sans">puzzles due now</p>
                        </div>

                        <div className="flex items-center gap-2">
                            <span className="text-2xl">🔥</span>
                            <div>
                                <p className="text-primary font-sans">
                                    {dashboardData.training_streak_days}-day streak
                                </p>
                                <p className="text-xs text-primary/60 font-sans">
                                    Keep it going!
                                </p>
                            </div>
                        </div>

                        {dashboardData.schedule.next_review_at && (
                            <p className="text-sm text-primary/60 font-sans">
                                Next review in {formatRelativeTime(dashboardData.schedule.next_review_at)}
                            </p>
                        )}

                        <button
                            type="button"
                            onClick={() => navigate('/puzzles')}
                            disabled={dashboardData.schedule.due_now === 0}
                            className={`w-full px-4 py-2 bg-accent text-bg-primary rounded-sm font-serif transition-colors km-focus-visible ${
                                dashboardData.schedule.due_now === 0 ? 'km-interactive-disabled disabled:opacity-50' : 'km-interactive'
                            }`}
                        >
                            Quick Session
                        </button>
                    </div>
                </section>
            </div>

            {/* First-Time User Prompt */}
            {dashboardData.total_sessions === 0 && (
                <section className="bg-primary/5 border border-primary/10 rounded-sm p-8 text-center">
                    <h3 className="text-2xl font-serif text-primary mb-4">
                        Ready to Start Training?
                    </h3>
                    <p className="text-primary/60 mb-6 font-sans">
                        You have {dashboardData.schedule.due_now} puzzles waiting.
                        Complete your first session to see your tactical vision!
                    </p>
                    <button
                        type="button"
                        onClick={() => navigate('/puzzles')}
                        className="px-8 py-3 bg-primary text-bg-primary rounded-sm text-lg font-serif km-interactive km-focus-visible"
                    >
                        Start First Session
                    </button>
                </section>
            )}

            {/* Recent Sessions */}
            {recentSessions.length > 0 && (
                <RecentSessionsCard sessions={recentSessions} />
            )}
        </main>
    );
}
