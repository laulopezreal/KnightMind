import { useState, useEffect, useCallback, useRef, type ReactNode } from 'react';
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
import { getTodaysFocus, type TodaysFocusResponse } from '../api/users';
import { getRecentSessions, type SessionSummary } from '../api/sessions';
import { formatMotifName, weakestMotif } from '../utils/motif';
import { TC_LABEL, type TimeControl } from '../utils/ratings';
import { useChessUsername } from '../context/ChessUsernameContext';
import { HeroTrainCard } from '../components/HeroTrainCard';
import { RecentlyTrickyCard } from '../components/RecentlyTrickyCard';
import { TodaysFocusCard } from '../components/TodaysFocusCard';
import { MomentumCard } from '../components/MomentumCard';
import { StreakCard } from '../components/StreakCard';
import { RecentSessionsCard } from '../components/RecentSessionsCard';
import { WeakestMotifCard } from '../components/WeakestMotifCard';
import { RatingDeltaCard } from '../components/RatingDeltaCard';
import { PageHeader } from '../components/PageHeader';
import { DataStateError, DataStateOffline, DataStateSkeleton } from '../components/DataState';
import { ConnectAccountEmpty } from '../components/ConnectAccountEmpty';
import { CardErrorBoundary } from '../components/CardErrorBoundary';
import { trainEntryDestination } from '../utils/trainEntry';
import { useOnlineStatus } from '../hooks/useOnlineStatus';
import { useLatestRequest } from '../hooks/useLatestRequest';

// The Rating tile mirrors whatever time control the Ratings page is set to, so
// the two surfaces agree. Read-only here (the Ratings page owns the setter).
const TIME_CONTROL_KEY = 'knightmind:ratings:time_control';
function readTimeControl(): TimeControl {
    const stored = localStorage.getItem(TIME_CONTROL_KEY);
    return stored === 'blitz' || stored === 'bullet' ? stored : 'rapid';
}

/**
 * Page shell. The header (h1 + subtitle) renders in EVERY state — loading,
 * offline, error, loaded — so the document always has a level-one heading.
 * Early-returning a bare state component dropped the h1 with the rest of the
 * page: a screen-reader user navigating by headings landed somewhere with no
 * identity ("something went wrong" — on which page?), and axe flagged
 * `page-has-heading-one`. Same structure as Insights.
 */
function DashboardShell({ children }: { children: ReactNode }) {
    return (
        <div className="container mx-auto p-6 max-w-7xl space-y-8 animate-teedin">
            <PageHeader title="Dashboard" subtitle="Your chess training overview" />
            {children}
        </div>
    );
}

/** Reliable weakest motif to lead with, or null when all-strong / no reliable data. */
function weakestReliable(resp: MotifPerformanceResponse | null) {
    const { weakest, allStrong } = weakestMotif(resp?.motifs ?? []);
    return allStrong ? null : weakest;
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
    const [todaysFocus, setTodaysFocus] = useState<TodaysFocusResponse | null>(null);
    const [stripLoading, setStripLoading] = useState(true);
    const timeControl = readTimeControl();

    const online = useOnlineStatus();
    const request = useLatestRequest();

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
        setTodaysFocus(null);

        Promise.allSettled([
            getMotifPerformance(username),
            getRatingExplain(username, timeControl),
            getUserStatus(username),
            getTodaysFocus(username),
        ]).then(([motif, rating, status, focus]) => {
            if (cancelled) return;
            if (motif.status === 'fulfilled') setMotifPerf(motif.value);
            if (rating.status === 'fulfilled') setRatingData(rating.value);
            if (status.status === 'fulfilled') setUserStatus(status.value);
            if (focus.status === 'fulfilled') setTodaysFocus(focus.value);
            setStripLoading(false);
        });

        return () => { cancelled = true; };
    }, [username, timeControl]);

    // No account connected. Explain in place instead of redirecting to Home:
    // the bounce announced nothing, so the sidebar link read as broken. Must
    // precede the loading branch — the fetch bails on an empty username, so
    // `loading` never clears and this state would otherwise skeleton forever.
    if (!username) {
        return (
            <DashboardShell>
                <ConnectAccountEmpty description="What's due, your streak, and how recent sessions went are all read from your own games. Connect your Chess.com account to fill this in." />
            </DashboardShell>
        );
    }

    if (loading) {
        // Skeleton mirrors the loaded layout (hero, tricky list, momentum/streak
        // grid) so the page doesn't collapse to a spinner and reflow. Uses the
        // shared DataStateSkeleton for the role="status" + sr-only announcement +
        // aria-hidden visual wrapper (same pattern as Home and the other pages).
        // No header placeholder block: the shell renders the real header above.
        return (
            <DashboardShell>
                <DataStateSkeleton label="Loading dashboard…" className="space-y-8 animate-pulse">
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
            </DashboardShell>
        );
    }

    if (error || !dashboardData) {
        // A failed load while the browser is offline is a connectivity problem,
        // not a server error — say so instead of a bare error message.
        return (
            <DashboardShell>
                {!online ? (
                    <DataStateOffline onRetry={loadDashboardData} />
                ) : (
                    <DataStateError
                        message={error || 'Failed to load dashboard data'}
                        onRetry={loadDashboardData}
                        retryLabel="Retry"
                        ariaLabel="Retry loading dashboard"
                    />
                )}
            </DashboardShell>
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

    // Only surface a tile once it has something real to say. Otherwise a
    // brand-new user (no games, no practised motifs) gets two dead "—" tiles
    // under the onboarding hero. The weakest tile deliberately stays for
    // mid-journey users (motifs practised but none yet reliable) as a "keep
    // going" nudge — that's informative, not clutter.
    const hasRatingTile = (ratingData?.stats.games ?? 0) > 0;
    const hasMotifTile = (motifPerf?.motifs.length ?? 0) > 0;
    const bothTiles = hasRatingTile && hasMotifTile;
    const showStrip = stripLoading || hasRatingTile || hasMotifTile;
    // Same rule as the strip above, which already hides itself with no data:
    // a tile that can only say "0%" teaches a new user nothing and reads as a
    // score they have somehow already earned.
    //
    // Gated on "has never trained", NOT on "the number is zero". A player who
    // trained last week and broke their streak genuinely has a 0-day streak,
    // and hiding that would delete real information.
    const hasMomentumTile = (dashboardData?.recent_form?.last_20_results?.length ?? 0) > 0;
    const hasStreakTile =
        (dashboardData?.training_streak_days ?? 0) > 0 ||
        dashboardData?.last_session_at != null;

    return (
        <DashboardShell>
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

            {/* Each tile below is wrapped in its own CardErrorBoundary. The page
                already drops a tile whose *data* failed (the allSettled loader
                above); this makes a tile whose *render* throws behave the same
                way, instead of costing the whole page. RouteErrorBoundary stays
                the net for anything outside a tile. */}

            {/* SECTION 1: Hero Train Card */}
            <CardErrorBoundary label="Training">
                <HeroTrainCard
                    dueCount={dashboardData.schedule.due_now}
                    dueIn4h={dashboardData.schedule.due_in_4h}
                    nextReviewAt={dashboardData.schedule.next_review_at}
                    needsWarmup={dashboardData.needs_warmup}
                    daysSinceLastSession={dashboardData.days_since_last_session}
                    totalSessions={dashboardData.total_sessions}
                    onStartSession={() => navigate(trainEntryDestination({
                        totalSessions: dashboardData.total_sessions,
                        dueCount: dashboardData.schedule.due_now,
                        needsWarmup: dashboardData.needs_warmup,
                    }))}
                    secondaryAction={heroSecondary}
                />
            </CardErrorBoundary>

            {/* IMPROVEMENT STRIP: outcome (rating Δ) + diagnosis (weakest motif) —
                the loop's "is it working?" and "what next?". Loads independently of
                the core dashboard; a failed slice simply omits its tile. */}
            {showStrip && (
                <div className={`grid grid-cols-1 gap-6 ${stripLoading || bothTiles ? 'md:grid-cols-2' : ''}`}>
                    {stripLoading ? (
                        <>
                            <div className="h-40 bg-primary/5 border border-primary/10 rounded-sm animate-pulse" aria-hidden="true" />
                            <div className="h-40 bg-primary/5 border border-primary/10 rounded-sm animate-pulse" aria-hidden="true" />
                        </>
                    ) : (
                        <>
                            {hasRatingTile && ratingData && (
                                <CardErrorBoundary label="Rating change">
                                    <RatingDeltaCard data={ratingData} timeControlLabel={TC_LABEL[timeControl]} />
                                </CardErrorBoundary>
                            )}
                            {hasMotifTile && motifPerf && (
                                <CardErrorBoundary label="Weakest motif">
                                    <WeakestMotifCard motifs={motifPerf.motifs} />
                                </CardErrorBoundary>
                            )}
                        </>
                    )}
                </div>
            )}

            {/* Today's focus — the daily card the spec asks for. Above Recently
                Tricky because it says what to do, not what happened. */}
            {todaysFocus && (
                <CardErrorBoundary label="Today's focus">
                    <TodaysFocusCard data={todaysFocus} />
                </CardErrorBoundary>
            )}

            {/* SECTION 2: Recently Tricky */}
            {trickyPuzzles && trickyPuzzles.puzzles.length > 0 && (
                <CardErrorBoundary label="Recently tricky">
                    <RecentlyTrickyCard puzzles={trickyPuzzles.puzzles} totalCount={trickyPuzzles.total_count} />
                </CardErrorBoundary>
            )}

            {/* SECTION 3 & 4: Two-column grid */}
            {(hasMomentumTile || hasStreakTile) && (
                <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
                    {hasMomentumTile && (
                        <div className={hasStreakTile ? 'md:col-span-2' : 'md:col-span-3'}>
                            <CardErrorBoundary label="Momentum">
                                <MomentumCard recentForm={dashboardData.recent_form} />
                            </CardErrorBoundary>
                        </div>
                    )}
                    {hasStreakTile && (
                        <div className={hasMomentumTile ? 'md:col-span-1' : 'md:col-span-3'}>
                            <CardErrorBoundary label="Consistency">
                                <StreakCard
                                    streakDays={dashboardData.training_streak_days}
                                    lastSessionAt={dashboardData.last_session_at}
                                />
                            </CardErrorBoundary>
                        </div>
                    )}
                </div>
            )}

            {/* SECTION 5: Recent Sessions (collapsible) */}
            {recentSessions.length > 0 && (
                <CardErrorBoundary label="Recent sessions">
                    <RecentSessionsCard
                        sessions={recentSessions}
                        collapsible={true}
                        defaultExpanded={false}
                    />
                </CardErrorBoundary>
            )}
        </DashboardShell>
    );
}
