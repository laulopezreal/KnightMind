import { useState, useEffect, useCallback, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import { getDashboardSummary, getTrickyPuzzles, type DashboardSummary, type TrickyPuzzlesResponse } from '../api/users';
import { getRecentSessions, type SessionSummary } from '../api/sessions';
import { useChessUsername } from '../context/ChessUsernameContext';
import { HeroTrainCard } from '../components/HeroTrainCard';
import { RecentlyTrickyCard } from '../components/RecentlyTrickyCard';
import { MomentumCard } from '../components/MomentumCard';
import { StreakCard } from '../components/StreakCard';
import { RecentSessionsCard } from '../components/RecentSessionsCard';
import { PageHeader } from '../components/PageHeader';
import { DataStateError, DataStateOffline, DataStateSkeleton } from '../components/DataState';
import { useOnlineStatus } from '../hooks/useOnlineStatus';
import { useLatestRequest } from '../hooks/useLatestRequest';

export default function Dashboard() {
    const { username } = useChessUsername();
    const navigate = useNavigate();

    const [dashboardData, setDashboardData] = useState<DashboardSummary | null>(null);
    const [trickyPuzzles, setTrickyPuzzles] = useState<TrickyPuzzlesResponse | null>(null);
    const [recentSessions, setRecentSessions] = useState<SessionSummary[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const hasLoadedRef = useRef(false);

    const online = useOnlineStatus();
    const request = useLatestRequest();

    // Redirect if no username
    useEffect(() => {
        if (!username) {
            navigate('/');
        }
    }, [username, navigate]);

    // Load all dashboard data - extracted for reusability
    const loadDashboardData = useCallback(async () => {
        if (!username) return;

        // Guard against stale-response races: a username change (or a focus
        // refresh) begins a newer request; the older, slower response must not
        // clobber the newer one.
        const token = request.begin();
        try {
            // Only show full-page spinner on initial load, not on background refreshes
            if (!hasLoadedRef.current) {
                setLoading(true);
            }
            setError(null);

            const [dashboard, sessions, tricky] = await Promise.all([
                getDashboardSummary(username),
                getRecentSessions(username, 5),
                getTrickyPuzzles(username, 5)
            ]);

            if (token.isStale()) return;
            setDashboardData(dashboard);
            setRecentSessions(sessions);
            setTrickyPuzzles(tricky);
            hasLoadedRef.current = true;
        } catch (err) {
            if (token.isStale()) return;
            console.error('Failed to load dashboard:', err);
            setError(err instanceof Error ? err.message : 'Failed to load dashboard data');
        } finally {
            if (!token.isStale()) setLoading(false);
        }
    }, [username, request]);

    // Initial load
    useEffect(() => {
        loadDashboardData();
    }, [loadDashboardData]);

    // Auto-refresh on window focus
    useEffect(() => {
        const handleFocus = () => {
            loadDashboardData();
        };

        window.addEventListener('focus', handleFocus);
        return () => {
            window.removeEventListener('focus', handleFocus);
        };
    }, [loadDashboardData]);

    if (loading) {
        return <DashboardSkeleton />;
    }

    if (error || !dashboardData) {
        // A failed load while the browser is offline is a connectivity problem,
        // not a server error — say so instead of a bare error message.
        return !online ? (
            <DataStateOffline onRetry={loadDashboardData} />
        ) : (
            <DataStateError
                message={error || 'Failed to load dashboard data'}
                onRetry={loadDashboardData}
                retryLabel="Retry"
                ariaLabel="Retry loading dashboard"
            />
        );
    }

    return (
        <div className="container mx-auto p-6 max-w-7xl space-y-8 animate-teedin">
            <PageHeader title="Dashboard" subtitle="Your chess training overview" />

            {/* SECTION 1: Hero Train Card */}
            <HeroTrainCard
                dueCount={dashboardData.schedule.due_now}
                dueIn4h={dashboardData.schedule.due_in_4h}
                nextReviewAt={dashboardData.schedule.next_review_at}
                needsWarmup={dashboardData.needs_warmup}
                daysSinceLastSession={dashboardData.days_since_last_session}
                totalSessions={dashboardData.total_sessions}
                onStartSession={() => navigate(dashboardData.needs_warmup ? '/puzzles?warmup=true' : '/puzzles')}
            />

            {/* SECTION 2: Recently Tricky */}
            {trickyPuzzles && trickyPuzzles.puzzles.length > 0 && (
                <RecentlyTrickyCard puzzles={trickyPuzzles.puzzles} totalCount={trickyPuzzles.total_count} />
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
        </div>
    );
}

/**
 * Loading skeleton that mirrors the real dashboard layout (hero, tricky list,
 * two-column momentum/streak grid). Mirroring the final structure keeps the
 * page from collapsing to a centered spinner and back — no jarring content
 * shift — and gives an honest sense of what is loading.
 */
function DashboardSkeleton() {
    return (
        <DataStateSkeleton
            label="Loading dashboard…"
            className="container mx-auto p-6 max-w-7xl space-y-8 animate-pulse"
        >
            {/* Page header */}
            <div className="space-y-2">
                <div className="h-9 w-56 bg-primary/10 rounded-sm" />
                <div className="h-4 w-72 max-w-full bg-primary/5 rounded-sm" />
            </div>

            {/* Hero */}
            <div className="h-40 bg-primary/10 rounded-sm" />

            {/* Recently tricky */}
            <div className="h-28 bg-primary/5 border border-primary/10 rounded-sm" />

            {/* Momentum (2 cols) + Streak (1 col) */}
            <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
                <div className="md:col-span-2 h-52 bg-primary/5 border border-primary/10 rounded-sm" />
                <div className="md:col-span-1 h-52 bg-primary/5 border border-primary/10 rounded-sm" />
            </div>
        </DataStateSkeleton>
    );
}
