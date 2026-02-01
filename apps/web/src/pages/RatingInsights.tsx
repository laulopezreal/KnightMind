import { useEffect, useState, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import { useChessUsername } from '../context/ChessUsernameContext';
import { createSnapshot, getRatingExplain, type ExplainResponse, type HighlightGame, type SnapshotResponse } from '../api/ratings';
import { getRecentSessions } from '../api/sessions';

const LS_KEYS = {
    RATINGS_TIME_CONTROL: 'knightmind:ratings:time_control',
    RATINGS_WINDOW: 'knightmind:ratings:window',
} as const;

// Confidence thresholds for Rating Insights
const LOW_CONFIDENCE_THRESHOLD = 10;
const HIGH_CONFIDENCE_THRESHOLD = 20;

export default function RatingInsights() {
    const navigate = useNavigate();
    const { username, setEditorOpen } = useChessUsername();
    const [data, setData] = useState<ExplainResponse | null>(null);
    const [loading, setLoading] = useState(false);
    const [snapshotLoading, setSnapshotLoading] = useState(false);
    const [snapshotSuccess, setSnapshotSuccess] = useState(false);
    const [snapshotError, setSnapshotError] = useState<string | null>(null);
    const [latestSnapshot, setLatestSnapshot] = useState<SnapshotResponse | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [timeControl, setTimeControlState] = useState<'rapid' | 'blitz'>(() => {
        const stored = localStorage.getItem(LS_KEYS.RATINGS_TIME_CONTROL);
        return stored === 'blitz' || stored === 'rapid' ? stored : 'rapid';
    });
    const [windowSource, setWindowSourceState] = useState<'session' | 'fallback_7d'>(() => {
        const stored = localStorage.getItem(LS_KEYS.RATINGS_WINDOW);
        return stored === 'last_7_days' ? 'fallback_7d' : 'session';
    });
    const setTimeControl = useCallback((value: 'rapid' | 'blitz') => {
        setTimeControlState(value);
        localStorage.setItem(LS_KEYS.RATINGS_TIME_CONTROL, value);
    }, []);
    const setWindowSource = useCallback((value: 'session' | 'fallback_7d') => {
        setWindowSourceState(value);
        localStorage.setItem(LS_KEYS.RATINGS_WINDOW, value === 'session' ? 'since_session' : 'last_7_days');
    }, []);
    const [hasSessions, setHasSessions] = useState<boolean | null>(null);
    const [sessionsLoading, setSessionsLoading] = useState(false);

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

    const checkSessions = useCallback(async () => {
        if (!username) return;
        setSessionsLoading(true);
        try {
            const sessions = await getRecentSessions(username, 1);
            setHasSessions(sessions.length > 0);
        } catch {
            // If sessions check fails, assume no sessions exist
            setHasSessions(false);
        } finally {
            setSessionsLoading(false);
        }
    }, [username]);

    useEffect(() => {
        if (username) {
            fetchData();
        }
    }, [username, timeControl, windowSource, fetchData]);

    useEffect(() => {
        if (username) {
            checkSessions();
        }
    }, [username, checkSessions]);

    // Auto-switch to "Last 7 Days" when no sessions exist; persist corrected value
    useEffect(() => {
        if (hasSessions === false && windowSource === 'session') {
            setWindowSource('fallback_7d');
        }
    }, [hasSessions, windowSource, setWindowSource]);

    const handleSnapshot = async () => {
        if (!username) return;
        setSnapshotLoading(true);
        setSnapshotError(null);
        try {
            const snapshotData = await createSnapshot(username, timeControl);
            setLatestSnapshot(snapshotData);
            setSnapshotSuccess(true);
            fetchData(); // Refresh data
        } catch (err) {
            setSnapshotError(err instanceof Error ? err.message : 'Failed to create snapshot');
        } finally {
            setSnapshotLoading(false);
        }
    };

    // Clear snapshotSuccess after 2 seconds (with cleanup to prevent memory leaks)
    useEffect(() => {
        if (snapshotSuccess) {
            const timer = setTimeout(() => {
                setSnapshotSuccess(false);
            }, 2000);
            return () => clearTimeout(timer);
        }
    }, [snapshotSuccess]);

    // Clear latestSnapshot card after 8 seconds (with cleanup)
    useEffect(() => {
        if (latestSnapshot) {
            const timer = setTimeout(() => {
                setLatestSnapshot(null);
            }, 8000);
            return () => clearTimeout(timer);
        }
    }, [latestSnapshot]);

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
    const isState0 = data && !hasGames;

    const N = data?.stats.games ?? 0;
    const confidence = N < LOW_CONFIDENCE_THRESHOLD ? 'low' : N < HIGH_CONFIDENCE_THRESHOLD ? 'medium' : 'high';
    const timeControlLabel = timeControl === 'rapid' ? 'Rapid' : 'Blitz';
    const windowLabel = N >= HIGH_CONFIDENCE_THRESHOLD ? `Last ${HIGH_CONFIDENCE_THRESHOLD} ${timeControlLabel} games` : `Last ${N} ${timeControlLabel} games`;
    const confidenceQualifier = confidence === 'low' ? 'Very small sample — insights are indicative only.' : confidence === 'medium' ? 'Moderate sample — trends may still be noisy.' : undefined;

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
                                type="button"
                                onClick={() => setWindowSource('session')}
                                disabled={hasSessions === false}
                                className={`km-toggle-option km-focus-visible px-3 py-2 text-sm font-sans transition-all rounded-sm ${windowSource === 'session' ? 'km-toggle-selected bg-primary text-bg-primary shadow-sm' : 'text-primary/60'} ${hasSessions === false ? 'km-interactive-disabled' : ''}`}
                            >
                                Since Session
                            </button>
                            <button
                                type="button"
                                onClick={() => setWindowSource('fallback_7d')}
                                className={`km-toggle-option km-focus-visible px-3 py-2 text-sm font-sans transition-all rounded-sm ${windowSource === 'fallback_7d' ? 'km-toggle-selected bg-primary text-bg-primary shadow-sm' : 'text-primary/60'}`}
                            >
                                Last 7 Days
                            </button>
                        </div>
                        {hasSessions === false && !sessionsLoading && (
                            <p className="text-[10px] text-primary/40 font-sans ml-1">
                                No sessions yet. Start a puzzle session to use session-based insights.{' '}
                                <button
                                    type="button"
                                    onClick={() => navigate('/puzzles')}
                                    className="km-interactive km-focus-visible km-inline-link text-primary text-[10px] font-medium"
                                >
                                    Start a session
                                </button>
                            </p>
                        )}
                        {sessionsLoading && (
                            <p className="text-[10px] text-primary/40 font-sans ml-1 uppercase tracking-wider">
                                Loading sessions...
                            </p>
                        )}
                        {hasSessions === true && (
                            <p className="text-[10px] text-primary/40 font-sans ml-1 uppercase tracking-wider">
                                Choose the time window used to explain rating changes.
                            </p>
                        )}
                    </div>

                    {/* Time Control Selector */}
                    <div className="flex flex-col gap-1.5">
                        <div className="flex bg-primary/5 rounded-sm p-1">
                            <button
                                type="button"
                                onClick={() => setTimeControl('rapid')}
                                className={`km-toggle-option km-focus-visible px-3 py-2 text-sm font-sans transition-all rounded-sm ${timeControl === 'rapid' ? 'km-toggle-selected bg-primary text-bg-primary shadow-sm' : 'text-primary/60'}`}
                            >
                                Rapid
                            </button>
                            <button
                                type="button"
                                onClick={() => setTimeControl('blitz')}
                                className={`km-toggle-option km-focus-visible px-3 py-2 text-sm font-sans transition-all rounded-sm ${timeControl === 'blitz' ? 'km-toggle-selected bg-primary text-bg-primary shadow-sm' : 'text-primary/60'}`}
                            >
                                Blitz
                            </button>
                        </div>
                        {data && data.stats.games === 0 && (
                            <div className="text-[10px] text-primary/40 font-sans ml-1">
                                <span>No data yet for {timeControl.charAt(0).toUpperCase() + timeControl.slice(1)}.</span>
                            </div>
                        )}
                    </div>

                    <div className="flex flex-col gap-1">
<button
                            type="button"
                            onClick={handleSnapshot}
                            disabled={snapshotLoading}
                            className={`km-focus-visible px-5 py-2 border border-primary/20 text-primary text-sm font-sans transition-all rounded-sm ${snapshotLoading ? 'km-interactive-disabled' : 'km-interactive'}`}
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
                    {/* STATE 0: No games */}
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
                                        type="button"
                                        onClick={handleSnapshot}
                                        disabled={snapshotLoading}
                                        className={`mt-2 px-6 py-2 bg-primary text-bg-primary text-sm font-sans transition-all rounded-sm km-focus-visible ${snapshotLoading ? 'km-interactive-disabled' : 'km-interactive'}`}
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

                    {/* STATE 1 & 2: Games exist */}
                    {hasGames && (
                        <>
                            {/* STATE 1 Callout - no longer needed since hasGames is the gate */}

                            {/* Summary Cards */}
                            <section className="grid grid-cols-1 md:grid-cols-4 gap-6">
                                <Card
                                    label="Net Change"
                                    value={hasSnapshots && data.rating.net_change !== null
                                        ? (data.rating.net_change > 0 ? `+${data.rating.net_change}` : `${data.rating.net_change}`)
                                        : "—"}
                                    sub={windowLabel}
                                    helper={confidenceQualifier}
                                    highlight={hasSnapshots && data.rating.net_change !== null && data.rating.net_change !== 0}
                                    positive={hasSnapshots && data.rating.net_change !== null && data.rating.net_change > 0}
                                    extra={!hasSnapshots ? "Record a snapshot to track rating change." : undefined}
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

                            {/* Reference rating note when estimated */}
                            {!hasSnapshots && data.rating.reference_is_approx && (
                                <p className="text-xs text-primary/40 font-sans italic mt-4">
                                    Reference rating is estimated from opponents.
                                </p>
                            )}

                            {/* Drivers */}
                            <section className="border-t border-primary/10 pt-8">
                                <h2 className="text-2xl font-serif text-primary mb-6">Primary Drivers</h2>
                                {hasGames ? (
                                    <>
                                        {data.drivers.length > 0 ? (
                                            (() => {
                                                const prefix = confidence === 'low' ? 'Early signal: ' : confidence === 'medium' ? 'Likely contributed: ' : 'Key driver: ';
                                                return (
                                                    <ul className="space-y-4">
                                                        {data.drivers.map((driver, i) => (
                                                            <li key={i} className="flex items-start gap-3 text-lg font-sans text-primary/80">
                                                                <span className="mt-2 w-1.5 h-1.5 rounded-full bg-primary/40" />
                                                                {prefix}{driver}
                                                            </li>
                                                        ))}
                                                    </ul>
                                                );
                                            })()
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

                            {/* Highlights - Only show if items exist */}
                            {hasGames && (data.highlights.worst_surprises.length > 0 || data.highlights.best_surprises.length > 0) && (
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

const Card = ({ label, value, sub, helper, highlight, positive, extra }: { label: string, value: string, sub?: string, helper?: string, highlight?: boolean, positive?: boolean, extra?: string }) => (
    <div className="p-6 bg-primary/5 rounded-sm border border-primary/10">
        <div className="text-xs font-sans uppercase tracking-widest text-primary/40 mb-2">{label}</div>
        <div className={`text-3xl font-serif mb-1 ${highlight ? (positive ? 'text-emerald-600' : 'text-red-500') : 'text-primary'}`}>
            {value}
        </div>
        {sub && <div className="text-xs font-sans text-primary/50 mb-1">{sub}</div>}
        {helper && <div className="text-xs font-sans text-primary/40 italic">{helper}</div>}
        {extra && <div className="text-xs font-sans text-primary/60 mt-2">{extra}</div>}
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
