import { useState, useEffect, useCallback, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import { getMotifPerformance, getMotifTrends, type MotifPerformanceResponse, type TrendsResponse } from '../api/users';
import { useChessUsername } from '../context/ChessUsernameContext';
import { TacticalRadar } from '../components/TacticalRadar';
import { MotifTrends } from '../components/MotifTrends';

export default function Insights() {
    const { username } = useChessUsername();
    const navigate = useNavigate();

    const [motifPerformance, setMotifPerformance] = useState<MotifPerformanceResponse | null>(null);
    const [trends, setTrends] = useState<TrendsResponse | null>(null);
    const [loading, setLoading] = useState(true);
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

    // Load insights data
    const loadInsightsData = useCallback(async () => {
        if (!username || isFetchingRef.current) return;

        isFetchingRef.current = true;
        try {
            setLoading(true);
            setError(null);

            const [motifs, trendsData] = await Promise.all([
                getMotifPerformance(username),
                getMotifTrends(username, 30)
            ]);

            if (isMountedRef.current) {
                setMotifPerformance(motifs);
                setTrends(trendsData);
            }
        } catch (err) {
            console.error('Failed to load insights:', err);
            if (isMountedRef.current) {
                setError(err instanceof Error ? err.message : 'Failed to load insights data');
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
        loadInsightsData();
    }, [loadInsightsData]);

    const handleMotifClick = (motif: string) => {
        // Navigate to puzzles page with motif filter
        navigate(`/puzzles?motif=${encodeURIComponent(motif)}`);
    };

    if (loading) {
        return (
            <div className="flex items-center justify-center min-h-screen" role="status" aria-live="polite">
                <div className="animate-spin h-12 w-12 border-4 border-primary/20 border-t-primary rounded-full" aria-hidden="true" />
                <span className="sr-only">Loading insights...</span>
            </div>
        );
    }

    if (error) {
        return (
            <div className="max-w-md mx-auto mt-32 text-center" role="alert" aria-live="assertive">
                <p className="text-red-500 mb-4">{error}</p>
                <button
                    type="button"
                    onClick={() => window.location.reload()}
                    className="px-6 py-2 border border-primary/20 rounded-sm km-interactive km-focus-visible"
                    aria-label="Retry loading insights"
                >
                    Retry
                </button>
            </div>
        );
    }

    const hasMotifs = motifPerformance && motifPerformance.motifs.length > 0;
    const hasTrends = trends && trends.motif_trends.length > 0;

    return (
        <main className="container mx-auto p-6 max-w-7xl space-y-12">
            {/* Header */}
            <section>
                <h1 className="text-4xl md:text-5xl font-serif text-primary mb-2">
                    Insights
                </h1>
                <p className="text-lg text-primary/60 font-sans">
                    Deep analysis of your puzzle performance
                </p>
            </section>

            {/* Puzzle Intelligence Section */}
            <section className="space-y-8">
                <h2 className="text-3xl font-serif text-primary border-b border-primary/10 pb-4">
                    Puzzle Intelligence
                </h2>

                {!hasMotifs && !hasTrends && (
                    <div className="bg-primary/5 border border-primary/10 rounded-sm p-12 text-center">
                        <p className="text-primary/60 font-sans text-lg mb-4">
                            No puzzle data yet
                        </p>
                        <p className="text-primary/40 font-sans text-sm mb-6">
                            Complete a few puzzle sessions to see your tactical patterns and trends.
                        </p>
                        <button
                            type="button"
                            onClick={() => navigate('/puzzles')}
                            className="px-6 py-2 bg-primary text-bg-primary rounded-sm font-serif km-interactive km-focus-visible"
                        >
                            Start Puzzles
                        </button>
                    </div>
                )}

                {/* Tactical Radar */}
                {hasMotifs && (
                    <TacticalRadar
                        motifs={motifPerformance.motifs}
                        onMotifClick={handleMotifClick}
                    />
                )}

                {/* Motif Trends */}
                {hasTrends && (
                    <MotifTrends
                        trends={trends.motif_trends}
                        windowDays={trends.window_days}
                    />
                )}
            </section>
        </main>
    );
}
