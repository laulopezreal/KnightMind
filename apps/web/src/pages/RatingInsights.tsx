import { useEffect, useState, useCallback } from 'react';
import { useChessUsername } from '../context/ChessUsernameContext';
import { createSnapshot, getRatingExplain, type ExplainResponse, type HighlightGame } from '../api/ratings';

export default function RatingInsights() {
    const { username, setEditorOpen } = useChessUsername();
    const [data, setData] = useState<ExplainResponse | null>(null);
    const [loading, setLoading] = useState(false);
    const [snapshotLoading, setSnapshotLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [timeControl, setTimeControl] = useState<'rapid' | 'blitz'>('rapid');
    const [windowSource, setWindowSource] = useState<'session' | 'fallback_7d'>('session');

    const fetchData = useCallback(async () => {
        if (!username) return;
        setLoading(true);
        setError(null);
        try {
            let sinceStr: string | undefined = undefined;
            const sinceSessionId: string | undefined = undefined;

            if (windowSource === 'fallback_7d') {
                const d = new Date();
                d.setDate(d.getDate() - 7);
                sinceStr = d.toISOString();
            }

            const resp = await getRatingExplain(username, timeControl, sinceSessionId, sinceStr);
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
        try {
            await createSnapshot(username, timeControl);
            fetchData(); // Refresh data
        } catch (err) {
            setError(err instanceof Error ? err.message : 'Failed to create snapshot');
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

    return (
        <div className="space-y-12 animate-teedin pb-20">
            <section className="flex flex-col md:flex-row justify-between items-end gap-6">
                <div>
                    <h1 className="text-4xl md:text-5xl font-serif text-primary mb-4">Rating Insights</h1>
                    <p className="text-lg text-primary/60 font-sans max-w-2xl">
                        Understand your progress using session-based drivers.
                    </p>
                </div>

                <div className="flex flex-wrap gap-4 items-center">
                    {/* Window Selector */}
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

                    <button
                        onClick={handleSnapshot}
                        disabled={snapshotLoading}
                        className="px-5 py-2 border border-primary/20 hover:bg-primary/5 text-primary text-sm font-sans transition-all disabled:opacity-50 ml-2"
                    >
                        {snapshotLoading ? 'Recording...' : 'Record Snapshot'}
                    </button>
                </div>
            </section>

            {error && <p className="text-red-500/80 font-sans">{error}</p>}

            {loading && !data && (
                <div className="py-20 text-center text-primary/40 font-serif animate-pulse">
                    Analyzing games...
                </div>
            )}

            {data && (
                <>
                    {/* Summary Cards */}
                    <section className="grid grid-cols-1 md:grid-cols-4 gap-6">
                        <Card
                            label="Net Change"
                            value={data.rating.net_change !== null ? (data.rating.net_change > 0 ? `+${data.rating.net_change}` : `${data.rating.net_change}`) : "No snapshots"}
                            sub={data.window.source === 'session' ? "Since last session" : "In selected window"}
                            highlight={data.rating.net_change !== null && data.rating.net_change !== 0}
                            positive={data.rating.net_change !== null && (data.rating.net_change || 0) > 0}
                        />
                        <Card
                            label="Performance"
                            value={`${data.stats.wins}W - ${data.stats.draws}D - ${data.stats.losses}L`}
                            sub={`${data.stats.games} games analyzed`}
                        />
                        <Card
                            label="Exp. vs Actual"
                            value={data.stats.expected_minus_actual !== null ? (data.stats.expected_minus_actual > 0 ? `+${(data.stats.expected_minus_actual || 0).toFixed(1)}` : `${(data.stats.expected_minus_actual || 0).toFixed(1)}`) : "-"}
                            sub="Score points diff"
                        />
                        <Card
                            label="Avg Opponent"
                            value={data.stats.avg_opponent_rating?.toString() || "-"}
                            sub={data.rating.reference_is_approx ? "(Approx reference)" : "vs Reference Rating"}
                        />
                    </section>

                    {/* Drivers */}
                    <section className="border-t border-primary/10 pt-8">
                        <h2 className="text-2xl font-serif text-primary mb-6">Primary Drivers</h2>
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
                            <p className="text-primary/50 font-sans italic">Not enough data to identify clear drivers.</p>
                        )}
                        <p className="mt-8 text-xs text-primary/40 font-sans italic">
                            * Drivers are based on results vs expectation; Chess.com uses internal factors, so this is directional.
                        </p>
                    </section>

                    {/* Highlights */}
                    <section className="grid grid-cols-1 md:grid-cols-2 gap-12 border-t border-primary/10 pt-8">
                        <div>
                            <h3 className="text-xl font-serif text-primary mb-6 flex items-center gap-2">
                                <span>Most Costly Games</span>
                                <span className="text-xs font-sans bg-red-500/10 text-red-600 px-2 py-1 rounded-full">Negative Surprise</span>
                            </h3>
                            <div className="space-y-2">
                                {data.highlights.worst_surprises.map(game => (
                                    <GameRow key={game.game_id} game={game} type="bad" />
                                ))}
                                {data.highlights.worst_surprises.length === 0 && <p className="text-primary/40 text-sm">No significant negative surprises.</p>}
                            </div>
                        </div>

                        <div>
                            <h3 className="text-xl font-serif text-primary mb-6 flex items-center gap-2">
                                <span>Most Helpful Games</span>
                                <span className="text-xs font-sans bg-emerald-500/10 text-emerald-600 px-2 py-1 rounded-full">Positive Surprise</span>
                            </h3>
                            <div className="space-y-2">
                                {data.highlights.best_surprises.map(game => (
                                    <GameRow key={game.game_id} game={game} type="good" />
                                ))}
                                {data.highlights.best_surprises.length === 0 && <p className="text-primary/40 text-sm">No significant positive surprises.</p>}
                            </div>
                        </div>
                    </section>
                </>
            )}
        </div>
    );
}

const Card = ({ label, value, sub, highlight, positive }: { label: string, value: string, sub?: string, highlight?: boolean, positive?: boolean }) => (
    <div className="p-6 bg-primary/5 rounded-sm border border-primary/10">
        <div className="text-xs font-sans uppercase tracking-widest text-primary/40 mb-2">{label}</div>
        <div className={`text-3xl font-serif mb-1 ${highlight ? (positive ? 'text-emerald-600' : 'text-red-500') : 'text-primary'}`}>
            {value}
        </div>
        {sub && <div className="text-xs font-sans text-primary/50">{sub}</div>}
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
