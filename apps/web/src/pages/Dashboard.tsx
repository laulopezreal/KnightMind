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
import { DataStateError, DataStateLoading } from '../components/DataState';

export default function Dashboard() {
    const { username } = useChessUsername();
    const navigate = useNavigate();

    const [dashboardData, setDashboardData] = useState<DashboardSummary | null>(null);
    const [trickyPuzzles, setTrickyPuzzles] = useState<TrickyPuzzlesResponse | null>(null);
    const [recentSessions, setRecentSessions] = useState<SessionSummary[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const isFetchingRef = useRef(false);
    const isMountedRef = useRef(true);
    const hasLoadedRef = useRef(false);

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
    const loadDashboardData = useCallback(async () => {
        // Guard against concurrent fetches
        if (!username || isFetchingRef.current) return;

        isFetchingRef.current = true;
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

            if (isMountedRef.current) {
                setDashboardData(dashboard);
                setRecentSessions(sessions);
                setTrickyPuzzles(tricky);
                hasLoadedRef.current = true;
            }
        } catch (err) {
            console.error('Failed to load dashboard:', err);
            if (isMountedRef.current) {
                setError(err instanceof Error ? err.message : 'Failed to load dashboard data');
            }
        } finally {
            if (isMountedRef.current) {
                setLoading(false);
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
            loadDashboardData();
        };

        window.addEventListener('focus', handleFocus);
        return () => {
            window.removeEventListener('focus', handleFocus);
        };
    }, [loadDashboardData]);

    if (loading) {
        return <DataStateLoading label="Loading dashboard..." />;
    }

    if (error || !dashboardData) {
        return (
            <DataStateError
                message={error || 'Failed to load dashboard data'}
                onRetry={loadDashboardData}
                retryLabel="Retry"
                ariaLabel="Retry loading dashboard"
            />
        );
    }

    return (
        <div className="container mx-auto p-6 max-w-7xl space-y-8">
            <PageHeader title="Dashboard" subtitle="Your chess training overview" />

            {/* SECTION 1: Hero Train Card */}
            <HeroTrainCard
                dueCount={dashboardData.schedule.due_now}
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
