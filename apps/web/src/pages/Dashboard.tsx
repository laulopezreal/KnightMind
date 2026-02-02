import { useState, useEffect, useCallback, useRef } from 'react';
import { useNavigate, Link } from 'react-router-dom';
import { getDashboardSummary, getTrickyPuzzles, type DashboardSummary, type TrickyPuzzlesResponse } from '../api/users';
import { getRecentSessions, type SessionSummary } from '../api/sessions';
import { useChessUsername } from '../context/ChessUsernameContext';
import { HeroTrainCard } from '../components/HeroTrainCard';
import { RecentlyTrickyCard } from '../components/RecentlyTrickyCard';
import { MomentumCard } from '../components/MomentumCard';
import { StreakCard } from '../components/StreakCard';
import { RecentSessionsCard } from '../components/RecentSessionsCard';

export default function Dashboard() {
    const { username } = useChessUsername();
    const navigate = useNavigate();

    const [dashboardData, setDashboardData] = useState<DashboardSummary | null>(null);
    const [trickyPuzzles, setTrickyPuzzles] = useState<TrickyPuzzlesResponse | null>(null);
    const [recentSessions, setRecentSessions] = useState<SessionSummary[]>([]);
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

            const [dashboard, sessions, tricky] = await Promise.all([
                getDashboardSummary(username),
                getRecentSessions(username, 5),
                getTrickyPuzzles(username, 5)
            ]);

            if (isMountedRef.current) {
                setDashboardData(dashboard);
                setRecentSessions(sessions);
                setTrickyPuzzles(tricky);
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
        <main className="container mx-auto p-6 max-w-7xl space-y-8">
            {/* Header */}
            <section>
                <h1 className="text-4xl md:text-5xl font-serif text-primary mb-2">
                    Dashboard
                </h1>
                <p className="text-lg text-primary/60 font-sans">
                    Your chess training overview
                </p>
            </section>

            {/* SECTION 1: Hero Train Card */}
            <HeroTrainCard
                dueCount={dashboardData.schedule.due_now}
                nextReviewAt={dashboardData.schedule.next_review_at}
                needsWarmup={dashboardData.needs_warmup}
                daysSinceLastSession={dashboardData.days_since_last_session}
                onStartSession={() => navigate(dashboardData.needs_warmup ? '/puzzles?warmup=true' : '/puzzles')}
            />

            {/* SECTION 2: Recently Tricky */}
            {trickyPuzzles && trickyPuzzles.puzzles.length > 0 && (
                <RecentlyTrickyCard puzzles={trickyPuzzles.puzzles} />
            )}

            {/* SECTION 3 & 4: Two-column grid */}
            <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
                <div className="md:col-span-2">
                    <MomentumCard recentForm={dashboardData.recent_form} />
                </div>
                <div className="md:col-span-1">
                    <StreakCard
                        streakDays={dashboardData.training_streak_days}
                        lastSessionAt={dashboardData.last_session_at}
                    />
                </div>
            </div>

            {/* SECTION 5: Recent Sessions (collapsible) */}
            {recentSessions.length > 0 && (
                <RecentSessionsCard
                    sessions={recentSessions}
                    collapsible={true}
                    defaultExpanded={false}
                />
            )}
        </main>
    );
}
