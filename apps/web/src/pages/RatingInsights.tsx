import { useEffect, useState, useCallback } from 'react';
import { useChessUsername } from '../context/ChessUsernameContext';
import { createSnapshot, getRatingExplain, type ExplainResponse, type HighlightGame, type SnapshotResponse } from '../api/ratings';

export default function RatingInsights() {
    const { username, setEditorOpen } = useChessUsername();
    const [data, setData] = useState<ExplainResponse | null>(null);
    const [loading, setLoading] = useState(false);
    const [snapshotLoading, setSnapshotLoading] = useState(false);
    const [snapshotSuccess, setSnapshotSuccess] = useState(false);
    const [snapshotError, setSnapshotError] = useState<string | null>(null);
    const [latestSnapshot, setLatestSnapshot] = useState<SnapshotResponse | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [timeControl, setTimeControl] = useState<'rapid' | 'blitz'>('rapid');
    const [windowSource, setWindowSource] = useState<'session' | 'fallback_7d'>('session');

    const fetchData = useCallback(async () => {
        if (!username) return;
        setLoading(true);
        setError(null);
        try {
            let sinceStr: string | undefined = undefined;

            if (windowSource === 'fallback_7d') {
                const d = new Date();
                d.setDate(d.getDate() - 7);
                sinceStr = d.toISOString();
            }

            const resp = await getRatingExplain(username, timeControl, undefined, sinceStr);
            setData(resp);
        } catch (err) {
            setError(err instanceof Error ? err.message : 'Failed to load insights');
        } finally {
            setLoading(false);
        }
    }, [username, timeControl, windowSource]);

    useEffect(() => {
        if (username) {
            fetchData();
        }
    }, [username, timeControl, windowSource, fetchData]);

    const handleSnapshot = async () => {
        if (!username) return;
        setSnapshotLoading(true);
        setSnapshotError(null);
        try {
            const snapshotData = await createSnapshot(username, timeControl);
            setLatestSnapshot(snapshotData);
            setSnapshotSuccess(true);
            fetchData(); // Refresh data
            // Show success for 2 seconds, then revert
            setTimeout(() => {
                setSnapshotSuccess(false);
            }, 2000);
        } catch (err) {
            setSnapshotError(err instanceof Error ? err.message : 'Failed to create snapshot');
        } finally {
            setSnapshotLoading(false);
        }
    };

    if (!username) {
        return (
            <div className="h-full flex flex-col justify-center items-center space-y-4">
                <p className="text-primary/60 font-sans text-lg">Please set a username to see rating insights.</p>
                <button
                    onClick={() => setEditorOpen(true)}
                    className="text-primary underline hover:text-primary/80 font-medium font-serif text-xl"
                >
                    Set Username
                </button>
            </div>
        );
    }

    const hasSnapshots = data?.rating.end !== null;
    const hasGames = (data?.stats.games || 0) > 0;
    const isState0 = data && !hasSnapshots;
    const isState1 = data && hasSnapshots && !hasGames;
    const isState2 = data && hasSnapshots && hasGames;

    return (
        <div className="space-y-12 animate-teedin pb-20">
            <section className="flex flex-col md:flex-row justify-between items-end gap-6">
                <div>
                    <h1 className="text-4xl md:text-5xl font-serif text-primary mb-4">Rating Insights</h1>
                    <p className="text-lg text-primary/60 font-sans max-w-2xl">
                        Understand your progress using session-based drivers.
                    </p>
                </div>

                <div className="flex flex-wrap gap-4 items-start">
                    {/* Window Selector */}
                    <div className="flex flex-col gap-1.5">
                        <div className="flex bg-primary/5 rounded-sm p-1">
                            <button
                                onClick={() => setWindowSource('session')}
                                className={`px-3 py-2 text-sm font-sans transition-all ${windowSource === 'session' ? 'bg-primary text-bg-primary shadow-sm' : 'text-primary/60 hover:text-primary'}`}
                            >
                                Since Session
                            </button>
                            <button
                                onClick={() => setWindowSource('fallback_7d')}
                                className={`px-3 py-2 text-sm font-sans transition-all ${windowSource === 'fallback_7d' ? 'bg-primary text-bg-primary shadow-sm' : 'text-primary/60 hover:text-primary'}`}
                            >
                                Last 7 Days
                            </button>
                        </div>
                        <p className="text-[10px] text-primary/40 font-sans ml-1 uppercase tracking-wider">
                            Choose the time window used to explain rating changes.
                        </p>
                    </div>

                    {/* Time Control Selector */}
                    <div className="flex bg-primary/5 rounded-sm p-1">
                        <button
                            onClick={() => setTimeControl('rapid')}
                            className={`px-3 py-2 text-sm font-sans transition-all ${timeControl === 'rapid' ? 'bg-primary text-bg-primary shadow-sm' : 'text-primary/60 hover:text-primary'}`}
                        >
                            Rapid
                        </button>
                        <button
                            onClick={() => setTimeControl('blitz')}
                            className={`px-3 py-2 text-sm font-sans transition-all ${timeControl === 'blitz' ? 'bg-primary text-bg-primary shadow-sm' : 'text-primary/60 hover:text-primary'}`}
                        >
                            Blitz
                        </button>
                    </div>

                    <div className="flex flex-col gap-1">
                        <button
                            onClick={handleSnapshot}
                            disabled={snapshotLoading}
                            className="px-5 py-2 border border-primary/20 hover:bg-primary/5 text-primary text-sm font-sans transition-all disabled:opacity-50"
                        >
                            {snapshotSuccess ? '✓ Snapshot recorded' : snapshotLoading ? 'Recording...' : 'Record Snapshot'}
                        </button>
                        {snapshotError && (
                            <p className="text-xs text-red-500/80 font-sans">
                                Could not record snapshot. Try again.
                            </p>
                        )}
                    </div>
                </div>
            </section>

            {error && <p className="text-red-500/80 font-sans">{error}</p>}

            {loading && !data && (
                <div className="py-20 text-center text-primary/40 font-serif animate-pulse">
                    Analyzing games...
                </div>
            )}

            {/* Latest Snapshot Confirmation Card */}
            {latestSnapshot && (
                <div className="p-4 border border-primary/10 bg-primary/5 rounded-sm max-w-md">
                    <h3 className="text-lg font-serif text-primary mb-1">Latest Snapshot</h3>
                    <p className="text-primary/80 font-sans text-sm">
                        {timeControl.charAt(0).toUpperCase() + timeControl.slice(1)} · {latestSnapshot.rating}
                    </p>
                    <p className="text-primary/50 font-sans text-xs">
                        Recorded just now
                    </p>
                    <p className="text-primary/60 font-sans text-sm mt-2">
                        Play a few {timeControl} games on Chess.com to unlock insights.
                    </p>
                </div>
            )}

            {data && (
                <>
                    {/* STATE 0: No snapshots */}
                    {isState0 && (
                        <div className="max-w-xl mx-auto bg-primary/5 border border-primary/10 p-12 rounded-sm space-y-10">
                            <div>
                                <h2 className="text-2xl font-serif text-primary mb-2">Rating Insights</h2>
                                <p className="text-primary/60 font-sans">See what influenced your rating changes over time.</p>
                            </div>

                            <div className="space-y-8">
                                <div className="space-y-2">
                                    <h3 className="font-serif text-lg text-primary">Step 1 — Record your first snapshot</h3>
                                    <p className="text-sm text-primary/60 font-sans leading-relaxed">
                                        This saves your current Chess.com rating so we can compare future progress.
                                    </p>
                                    <button
                                        onClick={handleSnapshot}
                                        disabled={snapshotLoading}
                                        className="mt-2 px-6 py-2 bg-primary text-bg-primary text-sm font-sans hover:bg-primary/90 transition-all disabled:opacity-50"
                                    >
                                        {snapshotLoading ? 'Recording...' : 'Record Snapshot'}
                                    </button>
                                </div>

                                <div className="space-y-2">
                                    <h3 className="font-serif text-lg text-primary">Step 2 — Play a few games</h3>
                                    <p className="text-sm text-primary/60 font-sans leading-relaxed">
                                        After you’ve played a few games, come back here to see what drove changes.
                                    </p>
                                </div>
                            </div>
                        </div>
                    )}

                    {/* STATE 1 & 2: Snapshots exist */}
                    {hasSnapshots && (
                        <>
                            {/* STATE 1 Callout */}
                            {isState1 && (
                                <div className="p-6 border border-primary/10 bg-primary/5 rounded-sm mb-12">
                                    <h3 className="text-lg font-serif text-primary mb-1">No games found in this window</h3>
                                    <p className="text-sm text-primary/60 font-sans">
                                        Play a few games on Chess.com, then return to see drivers and highlights.
                                    </p>
                                </div>
                            )}

                            {/* Summary Cards */}
                            <section className="grid grid-cols-1 md:grid-cols-4 gap-6">
                                <Card
                                    label="Net Change"
                                    value={hasGames && data.rating.net_change !== null
                                        ? (data.rating.net_change > 0 ? `+${data.rating.net_change}` : `${data.rating.net_change}`)
                                        : "—"}
                                    sub={data.window.source === 'session' ? "Since last session" : "In selected window"}
                                    highlight={hasGames && data.rating.net_change !== null && data.rating.net_change !== 0}
                                    positive={hasGames && data.rating.net_change !== null && (data.rating.net_change || 0) > 0}
                                />
                                <Card
                                    label="Performance"
                                    value={hasGames
                                        ? `${data.stats.wins}W - ${data.stats.draws}D - ${data.stats.losses}L`
                                        : "—"}
                                    sub={`${data.stats.games} games analyzed`}
                                />
                                <Card
                                    label="Performance vs Expectation"
                                    value={hasGames && data.stats.expected_minus_actual !== null
                                        ? (data.stats.expected_minus_actual > 0 ? `+${(data.stats.expected_minus_actual || 0).toFixed(1)}` : `${(data.stats.expected_minus_actual || 0).toFixed(1)}`)
                                        : "—"}
                                    sub="Expectation gap"
                                    helper="Positive means you outperformed expectations. Negative means you underperformed."
                                />
                                <Card
                                    label="Opponent Strength"
                                    value={hasGames ? (data.stats.avg_opponent_rating?.toString() || "—") : "—"}
                                    sub="Average opponent rating vs your reference rating."
                                />
                            </section>

                            {/* Drivers */}
                            <section className="border-t border-primary/10 pt-8">
                                <h2 className="text-2xl font-serif text-primary mb-6">Primary Drivers</h2>
                                {hasGames ? (
                                    <>
                                        {data.drivers.length > 0 ? (
                                            <ul className="space-y-4">
                                                {data.drivers.map((driver, i) => (
                                                    <li key={i} className="flex items-start gap-3 text-lg font-sans text-primary/80">
                                                        <span className="mt-2 w-1.5 h-1.5 rounded-full bg-primary/40" />
                                                        {driver}
                                                    </li>
                                                ))}
                                            </ul>
                                        ) : (
                                            <p className="text-primary/50 font-sans italic">No clear drivers yet. Play a few games and this will explain what influenced your rating most.</p>
                                        )}
                                    </>
                                ) : (
                                    <div className="space-y-2">
                                        <p className="text-primary/80 font-sans text-lg">No clear drivers yet.</p>
                                        <p className="text-primary/50 font-sans">Once you’ve played a few games, this section will explain what influenced your rating most.</p>
                                    </div>
                                )}
                                {hasGames && (
                                    <p className="mt-8 text-xs text-primary/40 font-sans italic">
                                        * Drivers are based on results vs expectation; Chess.com uses internal factors, so this is directional.
                                    </p>
                                )}
                            </section>

                            {/* Highlights - Only show if insights available and items exist */}
                            {isState2 && (data.highlights.worst_surprises.length > 0 || data.highlights.best_surprises.length > 0) && (
                                <section className="grid grid-cols-1 md:grid-cols-2 gap-12 border-t border-primary/10 pt-8">
                                    {data.highlights.worst_surprises.length > 0 && (
                                        <div>
                                            <h3 className="text-xl font-serif text-primary mb-6 flex items-center gap-2">
                                                <span>Most Costly Games</span>
                                                <span className="text-xs font-sans bg-red-500/10 text-red-600 px-2 py-1 rounded-full">Negative Surprise</span>
                                            </h3>
                                            <div className="space-y-2">
                                                {data.highlights.worst_surprises.map(game => (
                                                    <GameRow key={game.game_id} game={game} type="bad" />
                                                ))}
                                            </div>
                                        </div>
                                    )}

                                    {data.highlights.best_surprises.length > 0 && (
                                        <div>
                                            <h3 className="text-xl font-serif text-primary mb-6 flex items-center gap-2">
                                                <span>Most Helpful Games</span>
                                                <span className="text-xs font-sans bg-emerald-500/10 text-emerald-600 px-2 py-1 rounded-full">Positive Surprise</span>
                                            </h3>
                                            <div className="space-y-2">
                                                {data.highlights.best_surprises.map(game => (
                                                    <GameRow key={game.game_id} game={game} type="good" />
                                                ))}
                                            </div>
                                        </div>
                                    )}
                                </section>
                            )}
                        </>
                    )}
                </>
            )}
        </div>
    );
}

const Card = ({ label, value, sub, helper, highlight, positive }: { label: string, value: string, sub?: string, helper?: string, highlight?: boolean, positive?: boolean }) => (
    <div className="p-6 bg-primary/5 rounded-sm border border-primary/10">
        <div className="text-xs font-sans uppercase tracking-widest text-primary/40 mb-2">{label}</div>
        <div className={`text-3xl font-serif mb-1 ${highlight ? (positive ? 'text-emerald-600' : 'text-red-500') : 'text-primary'}`}>
            {value}
        </div>
        {sub && <div className="text-xs font-sans text-primary/50 mb-1">{sub}</div>}
        {helper && <div className="text-xs font-sans text-primary/40 italic">{helper}</div>}
    </div>
);

const GameRow = ({ game, type }: { game: HighlightGame, type: 'good' | 'bad' }) => (
    <a href={game.url} target="_blank" rel="noopener noreferrer" className="block p-4 hover:bg-primary/5 transition-colors border-b border-primary/5 group">
        <div className="flex justify-between items-center mb-1">
            <div className="font-medium text-primary/80 group-hover:text-primary transition-colors">
                vs {game.opponent_rating}
            </div>
            <div className={`text-sm font-bold ${type === 'good' ? 'text-emerald-600' : 'text-red-500'}`}>
                {game.result}
            </div>
        </div>
        <div className="flex justify-between items-center text-xs text-primary/50 font-sans">
            <div>Expected: {game.expected_score.toFixed(2)}</div>
            <div>{(Math.abs(game.rating_diff || 0))} pts {(game.rating_diff || 0) > 0 ? 'higher' : 'lower'}</div>
        </div>
    </a>
);
