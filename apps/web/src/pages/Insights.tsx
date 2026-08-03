import { useState, useEffect, useCallback, useRef, type ReactNode } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { getMistakeCauses, getMistakePatterns, getTodaysFocus, getMotifPerformance, getMotifTrends, getTrickyPuzzles, type MistakeCausesResponse, type MistakePatternsResponse, type TodaysFocusResponse, type MotifPerformanceResponse, type TrendsResponse, type TrickyPuzzlesResponse } from '../api/users';
import { useChessUsername } from '../context/ChessUsernameContext';
import { TacticalRadar } from '../components/TacticalRadar';
import { MistakePatternsCard } from '../components/MistakePatternsCard';
import { TodaysFocusCard } from '../components/TodaysFocusCard';
import { TopMistakeCausesCard } from '../components/TopMistakeCausesCard';
import { MotifTrends } from '../components/MotifTrends';
import { RecentlyTrickyCard } from '../components/RecentlyTrickyCard';
import { PageHeader } from '../components/PageHeader';
import { DataStateEmpty, DataStateError, DataStateLoading, DataStateOffline } from '../components/DataState';
import { ConnectAccountEmpty } from '../components/ConnectAccountEmpty';
import { useOnlineStatus } from '../hooks/useOnlineStatus';
import { useLatestRequest } from '../hooks/useLatestRequest';

/**
 * Page shell. The header (h1 + subtitle) renders in EVERY state — connect
 * account, loading, offline, error, empty, loaded — so the document always has
 * a level-one heading. Defined once here rather than repeated per branch, the
 * same way DashboardShell does it: this page grew a second copy of the header
 * as soon as it grew a second top-level return.
 */
function InsightsShell({ children }: { children: ReactNode }) {
    return (
        <div className="container mx-auto p-6 max-w-7xl space-y-8">
            <PageHeader title="Insights" subtitle="Deep analysis of your puzzle performance" />
            {children}
        </div>
    );
}

export default function Insights() {
    const { username } = useChessUsername();
    const navigate = useNavigate();

    const [motifPerformance, setMotifPerformance] = useState<MotifPerformanceResponse | null>(null);
    const [trends, setTrends] = useState<TrendsResponse | null>(null);
    const [trickyPuzzles, setTrickyPuzzles] = useState<TrickyPuzzlesResponse | null>(null);
    const [causes, setCauses] = useState<MistakeCausesResponse | null>(null);
    const [patterns, setPatterns] = useState<MistakePatternsResponse | null>(null);
    const [focus, setFocus] = useState<TodaysFocusResponse | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const hasLoadedRef = useRef(false);

    const online = useOnlineStatus();
    const request = useLatestRequest();

    // Load insights data
    const loadInsightsData = useCallback(async () => {
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

            const [motifs, trendsData] = await Promise.all([
                getMotifPerformance(username),
                getMotifTrends(username, 30)
            ]);

            // Supplementary: fail silently — page works without them
            const [tricky, causeData, patternData, focusData] = await Promise.all([
                getTrickyPuzzles(username, 5).catch(() => null),
                getMistakeCauses(username).catch(() => null),
                getMistakePatterns(username).catch(() => null),
                getTodaysFocus(username).catch(() => null),
            ]);

            if (token.isStale()) return;
            setMotifPerformance(motifs);
            setTrends(trendsData);
            setTrickyPuzzles(tricky);
            setCauses(causeData);
            setPatterns(patternData);
            setFocus(focusData);
            hasLoadedRef.current = true;
        } catch (err) {
            if (token.isStale()) return;
            console.error('Failed to load insights:', err);
            setError(err instanceof Error ? err.message : 'Failed to load insights data');
        } finally {
            if (!token.isStale()) setLoading(false);
        }
    }, [username, request]);

    // Initial load
    useEffect(() => {
        loadInsightsData();
    }, [loadInsightsData]);

    // Auto-refresh on window focus
    useEffect(() => {
        const handleFocus = () => {
            loadInsightsData();
        };

        window.addEventListener('focus', handleFocus);
        return () => {
            window.removeEventListener('focus', handleFocus);
        };
    }, [loadInsightsData]);

    const handleMotifClick = (motif: string) => {
        // Navigate to puzzles page with motif filter
        navigate(`/puzzles?motif=${encodeURIComponent(motif)}`);
    };

    const hasMotifs = motifPerformance && motifPerformance.motifs.length > 0;
    // A successful but empty response is not "has causes" — treating it as
    // such suppressed the page-level empty state and its Start Puzzles CTA for
    // brand-new accounts. Pending work counts, though: "12 still to analyse" is
    // exactly what a user with no causes yet needs to see.
    const hasCauses = !!causes && (causes.causes.length > 0 || causes.pending > 0);
    const hasTrends = trends && trends.motif_trends.length > 0;
    const hasTrickyPuzzles = trickyPuzzles && trickyPuzzles.puzzles.length > 0;

    // No account connected. Explain in place instead of redirecting to Home —
    // the bounce announced nothing, so the sidebar link read as broken.
    if (!username) {
        return (
            <InsightsShell>
                <ConnectAccountEmpty description="Insights read the tactical patterns out of your own games and puzzle history. Connect your Chess.com account to start building them." />
            </InsightsShell>
        );
    }

    return (
        <InsightsShell>
            {loading ? (
                <DataStateLoading label="Loading insights..." />
            ) : error ? (
                !online ? (
                    <DataStateOffline onRetry={loadInsightsData} />
                ) : (
                    <DataStateError
                        message={error}
                        onRetry={loadInsightsData}
                        retryLabel="Retry"
                        ariaLabel="Retry loading insights"
                    />
                )
            ) : (
                <>
                    {/* Empty state — only when no data at all */}
                    {!hasMotifs && !hasTrends && !hasCauses && (
                        <DataStateEmpty
                            title="No puzzle data yet"
                            description="Complete a few puzzle sessions to see your tactical patterns and trends."
                            actionLabel="Start Puzzles"
                            onAction={() => navigate('/puzzles')}
                        />
                    )}

                    {/* Tier 0: why the mistakes happen. Above the radar because a
                        cause is actionable in a way a motif label is not — "you
                        don't scan for loose pieces" tells you what to change,
                        "fork: 40%" does not. */}
                    {/* Named habits lead: "Loose Piece Syndrome, and here is what
                        to change" is more actionable than the cause breakdown
                        beneath it, which is the same information unnamed. */}
                    {/* The recommendation comes before the description of the
                        habits: a user who reads one card should read the one
                        that says what to do. */}
                    {focus && <TodaysFocusCard data={focus} />}

                    {patterns && <MistakePatternsCard data={patterns} />}

                    {hasCauses && <TopMistakeCausesCard data={causes} />}

                    {/* Tier 1: Tactical Radar — full width, visually dominant */}
                    {hasMotifs && (
                        <TacticalRadar
                            motifs={motifPerformance.motifs}
                            onMotifClick={handleMotifClick}
                        />
                    )}

                    {/* Tier 2: Supporting content — trends (2/3) + tricky puzzles (1/3) */}
                    {(hasTrends || hasTrickyPuzzles) && (
                        <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
                            {hasTrends && (
                                <div className={hasTrickyPuzzles ? 'md:col-span-2' : 'md:col-span-3'}>
                                    <MotifTrends
                                        trends={trends.motif_trends}
                                        windowDays={trends.window_days}
                                    />
                                </div>
                            )}
                            {hasTrickyPuzzles && (
                                <div className={hasTrends ? 'md:col-span-1' : 'md:col-span-3'}>
                                    <RecentlyTrickyCard
                                        puzzles={trickyPuzzles.puzzles}
                                        totalCount={trickyPuzzles.total_count}
                                    />
                                </div>
                            )}
                        </div>
                    )}

                    {/* These insights are tactical; where the mistakes start is
                        an opening question, and the Explorer had no inbound
                        route from anywhere but Home and the sidebar. */}
                    <section className="flex flex-wrap items-center justify-between gap-3 border-t border-primary/10 pt-6">
                        <p className="text-sm font-sans text-primary/70">
                            Patterns above are tactical. See which openings they keep coming from.
                        </p>
                        <Link
                            to="/openings"
                            className="km-interactive km-focus-visible text-sm font-serif px-4 py-1.5 border border-primary/20 text-primary rounded-sm transition-all shrink-0"
                        >
                            Opening Explorer →
                        </Link>
                    </section>
                </>
            )}
        </InsightsShell>
    );
}
