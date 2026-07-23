import { useState, useEffect, useCallback, useRef } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import {
    getDashboardSummary,
    getTrickyPuzzles,
    getMotifPerformance,
    getUserStatus,
    type DashboardSummary,
    type TrickyPuzzlesResponse,
    type MotifPerformanceResponse,
    type UserStatus,
} from '../api/users';
import { getRatingExplain, type ExplainResponse } from '../api/ratings';
import { getRecentSessions, type SessionSummary } from '../api/sessions';
import { formatMotifName } from '../utils/motif';
import { useChessUsername } from '../context/ChessUsernameContext';
import { HeroTrainCard } from '../components/HeroTrainCard';
import { RecentlyTrickyCard } from '../components/RecentlyTrickyCard';
import { MomentumCard } from '../components/MomentumCard';
import { StreakCard } from '../components/StreakCard';
import { RecentSessionsCard } from '../components/RecentSessionsCard';
import { WeakestMotifCard } from '../components/WeakestMotifCard';
import { RatingDeltaCard } from '../components/RatingDeltaCard';
import { PageHeader } from '../components/PageHeader';
import { DataStateError, DataStateOffline, DataStateSkeleton } from '../components/DataState';
import { useOnlineStatus } from '../hooks/useOnlineStatus';
import { useLatestRequest } from '../hooks/useLatestRequest';

// The Rating tile mirrors whatever time control the Ratings page is set to, so
// the two surfaces agree. Read-only here (the Ratings page owns the setter).
const TIME_CONTROL_KEY = 'knightmind:ratings:time_control';
function readTimeControl(): 'rapid' | 'blitz' | 'bullet' {
    const stored = localStorage.getItem(TIME_CONTROL_KEY);
    return stored === 'blitz' || stored === 'bullet' ? stored : 'rapid';
}
const TC_LABEL = { rapid: 'Rapid', blitz: 'Blitz', bullet: 'Bullet' } as const;

/** Reliable weakest motif (enough attempts), or null — mirrors the Insights rule. */
function weakestReliable(resp: MotifPerformanceResponse | null) {
    const reliable = (resp?.motifs ?? []).filter((m) => !m.insufficient_data);
    if (reliable.length === 0 || reliable.every((m) => m.accuracy >= 0.85)) return null;
    return reliable.reduce((min, m) => (m.accuracy < min.accuracy ? m : min));
}

export default function Dashboard() {
    const { username } = useChessUsername();
    const navigate = useNavigate();

    const [dashboardData, setDashboardData] = useState<DashboardSummary | null>(null);
    const [trickyPuzzles, setTrickyPuzzles] = useState<TrickyPuzzlesResponse | null>(null);
    const [recentSessions, setRecentSessions] = useState<SessionSummary[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const hasLoadedRef = useRef(false);

    // "Improvement strip" data — the motif diagnosis + rating outcome that tie
    // the training loop together. Fetched SEPARATELY from the core dashboard so a
    // slow/failed rating analysis never blocks or errors the primary page. Each
    // tile renders independently; a null slice just omits its tile.
    const [motifPerf, setMotifPerf] = useState<MotifPerformanceResponse | null>(null);
    const [ratingData, setRatingData] = useState<ExplainResponse | null>(null);
    const [userStatus, setUserStatus] = useState<UserStatus | null>(null);
    const [stripLoading, setStripLoading] = useState(true);
    const timeControl = readTimeControl();

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

    // Secondary load: improvement-strip data. allSettled so one failing call
    // (e.g. the heavier rating analysis) doesn't take down the others. Runs once
    // per username — deliberately NOT on focus, to avoid re-running rating
    // analysis on every tab switch.
    useEffect(() => {
        if (!username) return;
        let cancelled = false;
        setStripLoading(true);
        setMotifPerf(null);
        setRatingData(null);
        setUserStatus(null);

        Promise.allSettled([
            getMotifPerformance(username),
            getRatingExplain(username, timeControl),
            getUserStatus(username),
        ]).then(([motif, rating, status]) => {
            if (cancelled) return;
            if (motif.status === 'fulfilled') setMotifPerf(motif.value);
            if (rating.status === 'fulfilled') setRatingData(rating.value);
            if (status.status === 'fulfilled') setUserStatus(status.value);
            setStripLoading(false);
        });

        return () => { cancelled = true; };
    }, [username, timeControl]);

    if (loading) {
        // Skeleton mirrors the loaded layout (hero, tricky list, momentum/streak
        // grid) so the page doesn't collapse to a spinner and reflow. Uses the
        // shared DataStateSkeleton for the role="status" + sr-only announcement +
        // aria-hidden visual wrapper (same pattern as Home and the other pages).
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

    // Smart hero shortcut: in the everyday "Train Today" state, offer a one-click
    // targeted session on the user's weakest motif. Suppressed for first-timers,
    // warmups, and caught-up (0 due) — where a different action already leads.
    const weakest = weakestReliable(motifPerf);
    const heroSecondary = weakest
        && dashboardData.total_sessions > 0
        && !dashboardData.needs_warmup
        && dashboardData.schedule.due_now > 0
        ? {
            label: `Or train your weakest: ${formatMotifName(weakest.name)}`,
            onClick: () => navigate(`/puzzles?motif=${encodeURIComponent(weakest.name)}`),
        }
        : undefined;

    const showStrip = stripLoading || ratingData != null || motifPerf != null;

    return (
        <div className="container mx-auto p-6 max-w-7xl space-y-8 animate-teedin">
            <PageHeader title="Dashboard" subtitle="Your chess training overview" />

            {/* New games waiting to be imported into training. */}
            {userStatus?.has_new_games && (
                <div className="flex items-center justify-between gap-3 bg-primary/5 border border-primary/10 rounded-sm px-4 py-3">
                    <p className="text-sm font-sans text-primary/80">
                        New games are ready to import into your training.
                    </p>
                    <Link
                        to="/"
                        className="km-interactive km-focus-visible text-sm font-serif px-4 py-1.5 border border-primary/20 text-primary rounded-sm hover:bg-primary hover:text-bg-primary hover:border-transparent transition-all shrink-0"
                    >
                        Import
                    </Link>
                </div>
            )}

            {/* SECTION 1: Hero Train Card */}
            <HeroTrainCard
                dueCount={dashboardData.schedule.due_now}
                dueIn4h={dashboardData.schedule.due_in_4h}
                nextReviewAt={dashboardData.schedule.next_review_at}
                needsWarmup={dashboardData.needs_warmup}
                daysSinceLastSession={dashboardData.days_since_last_session}
                totalSessions={dashboardData.total_sessions}
                onStartSession={() => navigate(dashboardData.needs_warmup ? '/puzzles?warmup=true' : '/puzzles')}
                secondaryAction={heroSecondary}
            />

            {/* IMPROVEMENT STRIP: outcome (rating Δ) + diagnosis (weakest motif) —
                the loop's "is it working?" and "what next?". Loads independently of
                the core dashboard; a failed slice simply omits its tile. */}
            {showStrip && (
                <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                    {stripLoading ? (
                        <>
                            <div className="h-40 bg-primary/5 border border-primary/10 rounded-sm animate-pulse" aria-hidden="true" />
                            <div className="h-40 bg-primary/5 border border-primary/10 rounded-sm animate-pulse" aria-hidden="true" />
                        </>
                    ) : (
                        <>
                            {ratingData && <RatingDeltaCard data={ratingData} timeControlLabel={TC_LABEL[timeControl]} />}
                            {motifPerf && <WeakestMotifCard motifs={motifPerf.motifs} />}
                        </>
                    )}
                </div>
            )}

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
