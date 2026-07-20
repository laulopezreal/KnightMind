import { useState, useEffect, useCallback, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import { getMotifPerformance, getMotifTrends, getTrickyPuzzles, type MotifPerformanceResponse, type TrendsResponse, type TrickyPuzzlesResponse } from '../api/users';
import { useChessUsername } from '../context/ChessUsernameContext';
import { TacticalRadar } from '../components/TacticalRadar';
import { MotifTrends } from '../components/MotifTrends';
import { RecentlyTrickyCard } from '../components/RecentlyTrickyCard';
import { PageHeader } from '../components/PageHeader';
import { DataStateEmpty, DataStateError, DataStateLoading, DataStateOffline } from '../components/DataState';
import { useOnlineStatus } from '../hooks/useOnlineStatus';
import { useLatestRequest } from '../hooks/useLatestRequest';

export default function Insights() {
    const { username } = useChessUsername();
    const navigate = useNavigate();

    const [motifPerformance, setMotifPerformance] = useState<MotifPerformanceResponse | null>(null);
    const [trends, setTrends] = useState<TrendsResponse | null>(null);
    const [trickyPuzzles, setTrickyPuzzles] = useState<TrickyPuzzlesResponse | null>(null);
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

            // Supplementary: fail silently — page works without it
            const tricky = await getTrickyPuzzles(username, 5).catch(() => null);

            if (token.isStale()) return;
            setMotifPerformance(motifs);
            setTrends(trendsData);
            setTrickyPuzzles(tricky);
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
    const hasTrends = trends && trends.motif_trends.length > 0;
    const hasTrickyPuzzles = trickyPuzzles && trickyPuzzles.puzzles.length > 0;

    return (
        <div className="container mx-auto p-6 max-w-7xl space-y-8">
            <PageHeader title="Insights" subtitle="Deep analysis of your puzzle performance" />

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
                    {!hasMotifs && !hasTrends && (
                        <DataStateEmpty
                            title="No puzzle data yet"
                            description="Complete a few puzzle sessions to see your tactical patterns and trends."
                            actionLabel="Start Puzzles"
                            onAction={() => navigate('/puzzles')}
                        />
                    )}

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
                </>
            )}
        </div>
    );
}
