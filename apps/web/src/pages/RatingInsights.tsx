import { LOCALE } from '../utils/locale';
import { useEffect, useState, useCallback, useMemo } from 'react';
import { useNavigate } from 'react-router-dom';
import { LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer, ReferenceDot } from 'recharts';
import { useChessUsername } from '../context/ChessUsernameContext';
import { createSnapshot, getRatingExplain, getRatingHistory, type ExplainResponse, type HighlightGame, type SnapshotResponse, type SnapshotHistoryItem } from '../api/ratings';
import { getRecentSessions } from '../api/sessions';
import { PageHeader } from '../components/PageHeader';
import { DataStateError, DataStateLoading } from '../components/DataState';

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
    const [timeControl, setTimeControlState] = useState<'rapid' | 'blitz' | 'bullet'>(() => {
        const stored = localStorage.getItem(LS_KEYS.RATINGS_TIME_CONTROL);
        return stored === 'blitz' || stored === 'rapid' || stored === 'bullet' ? stored : 'rapid';
    });
    const [windowSource, setWindowSourceState] = useState<'session' | 'fallback_7d'>(() => {
        const stored = localStorage.getItem(LS_KEYS.RATINGS_WINDOW);
        return stored === 'last_7_days' ? 'fallback_7d' : 'session';
    });
    const setTimeControl = useCallback((value: 'rapid' | 'blitz' | 'bullet') => {
        setTimeControlState(value);
        localStorage.setItem(LS_KEYS.RATINGS_TIME_CONTROL, value);
    }, []);
    const setWindowSource = useCallback((value: 'session' | 'fallback_7d') => {
        setWindowSourceState(value);
        localStorage.setItem(LS_KEYS.RATINGS_WINDOW, value === 'session' ? 'since_session' : 'last_7_days');
    }, []);
    const [hasSessions, setHasSessions] = useState<boolean | null>(null);
    const [lastSessionId, setLastSessionId] = useState<string | null>(null);
    const [sessionsLoading, setSessionsLoading] = useState(false);
    const [history, setHistory] = useState<SnapshotHistoryItem[]>([]);

    const fetchData = useCallback(async (resolvedSessionId?: string | null) => {
        if (!username) return;
        setLoading(true);
        setError(null);
        try {
            let sinceStr: string | undefined = undefined;
            let sessionId: string | undefined = undefined;

            const effectiveSessionId = resolvedSessionId !== undefined ? resolvedSessionId : lastSessionId;

            if (windowSource === 'fallback_7d') {
                const d = new Date();
                d.setDate(d.getDate() - 7);
                sinceStr = d.toISOString();
            } else if (windowSource === 'session' && effectiveSessionId) {
                sessionId = effectiveSessionId;
            }

            const [resp, historyData] = await Promise.all([
                getRatingExplain(username, timeControl, sessionId, sinceStr),
                getRatingHistory(username, timeControl),
            ]);
            setData(resp);
            setHistory(historyData);
        } catch (err) {
            setError(err instanceof Error ? err.message : 'Failed to load insights');
        } finally {
            setLoading(false);
        }
    }, [username, timeControl, windowSource, lastSessionId]);

    // Single coordinated effect: fetch sessions first, then data with the resolved session ID.
    // Also handles auto-switch to "Last 7 Days" when no sessions exist, to avoid a
    // wasted fetch with the wrong windowSource.
    useEffect(() => {
        if (!username) return;
        let cancelled = false;

        async function loadAll() {
            // 1. Check sessions
            setSessionsLoading(true);
            let resolvedSessionId: string | null = null;
            let foundSessions = false;
            try {
                const sessions = await getRecentSessions(username, 1);
                if (cancelled) return;
                foundSessions = sessions.length > 0;
                setHasSessions(foundSessions);
                resolvedSessionId = foundSessions ? sessions[0].session_id : null;
                setLastSessionId(resolvedSessionId);
            } catch {
                if (cancelled) return;
                setHasSessions(false);
                setLastSessionId(null);
            } finally {
                if (!cancelled) setSessionsLoading(false);
            }

            // Auto-switch to fallback_7d when no sessions and currently on session mode.
            // This prevents a wasted fetch with session mode when there are no sessions.
            if (!foundSessions && windowSource === 'session') {
                if (!cancelled) setWindowSource('fallback_7d');
                // The windowSource change will re-trigger this effect with the correct mode.
                return;
            }

            // 2. Fetch data with resolved session ID
            if (!cancelled) {
                await fetchData(resolvedSessionId);
            }
        }

        loadAll();
        return () => { cancelled = true; };
    }, [username, timeControl, windowSource, fetchData, setWindowSource]);

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

    // Memoize chart data to avoid re-creating objects on every render.
    // Uses index-based labels to guarantee unique X-axis keys even when
    // multiple snapshots fall on the same calendar day.
    const chartData = useMemo(() => {
        const dateCounts = new Map<string, number>();
        return history.map(h => {
            const base = new Date(h.recorded_at).toLocaleDateString(LOCALE, { month: 'short', day: 'numeric' });
            const count = (dateCounts.get(base) ?? 0) + 1;
            dateCounts.set(base, count);
            return { label: count > 1 ? `${base} (${count})` : base, rating: h.rating };
        });
    }, [history]);

    const chartTrendColor = useMemo(() => {
        if (history.length < 2) return '#059669';
        return history[history.length - 1].rating >= history[0].rating ? '#059669' : '#ef4444';
    }, [history]);

    if (!username) {
        return (
            <div className="h-full flex flex-col justify-center items-center space-y-4">
                <p className="text-primary/70 font-sans text-lg">Please set a username to see rating insights.</p>
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
    const timeControlLabel = timeControl === 'rapid' ? 'Rapid' : timeControl === 'blitz' ? 'Blitz' : 'Bullet';
    const windowLabel = N >= HIGH_CONFIDENCE_THRESHOLD ? `Last ${HIGH_CONFIDENCE_THRESHOLD} ${timeControlLabel} games` : `Last ${N} ${timeControlLabel} games`;

    const formatDate = (iso: string) => {
        const d = new Date(iso);
        return d.toLocaleDateString(LOCALE, { month: 'short', day: 'numeric', year: 'numeric' });
    };
    const windowDates = data?.window
        ? `${formatDate(data.window.start)} – ${formatDate(data.window.end)}`
        : undefined;

    const confidenceBadge = confidence === 'low'
        ? { label: 'Low confidence', color: 'bg-negative-soft text-negative' }
        : confidence === 'medium'
        ? { label: 'Medium confidence', color: 'bg-status-learning-soft text-status-learning' }
        : { label: 'High confidence', color: 'bg-positive-soft text-positive' };

    return (
        <div className="space-y-12 animate-teedin pb-20">
            <section className="flex flex-col md:flex-row justify-between items-end gap-6">
                <PageHeader
                    title="Rating Insights"
                    subtitle="Understand your progress using session-based drivers."
                />

                <div className="flex flex-wrap gap-4 items-start">
                    {/* Window Selector */}
                    <div className="flex flex-col gap-1.5">
                        <div className="flex bg-primary/5 rounded-sm p-1">
                            <button
                                type="button"
                                onClick={() => setWindowSource('session')}
                                disabled={hasSessions === false}
                                aria-pressed={windowSource === 'session'}
                                className={`km-toggle-option km-focus-visible px-3 py-2 text-sm font-sans transition-all rounded-sm ${windowSource === 'session' ? 'km-toggle-selected bg-primary text-bg-primary shadow-sm' : 'text-primary/70'} ${hasSessions === false ? 'km-interactive-disabled' : ''}`}
                            >
                                Since Session
                            </button>
                            <button
                                type="button"
                                onClick={() => setWindowSource('fallback_7d')}
                                aria-pressed={windowSource === 'fallback_7d'}
                                className={`km-toggle-option km-focus-visible px-3 py-2 text-sm font-sans transition-all rounded-sm ${windowSource === 'fallback_7d' ? 'km-toggle-selected bg-primary text-bg-primary shadow-sm' : 'text-primary/70'}`}
                            >
                                Last 7 Days
                            </button>
                        </div>
                        {hasSessions === false && !sessionsLoading && (
                            <div className="flex flex-col items-start gap-1.5 ml-1">
                                <p className="text-[10px] text-primary/70 font-sans">
                                    No sessions yet. Start a puzzle session to use session-based insights.
                                </p>
                                <button
                                    type="button"
                                    onClick={() => navigate('/puzzles')}
                                    className="km-interactive km-focus-visible km-inline-link text-primary text-xs font-medium underline decoration-primary/30 underline-offset-4 transition-colors"
                                >
                                    Start a session
                                </button>
                            </div>
                        )}
                        {sessionsLoading && (
                            <div className="ml-1">
                                <DataStateLoading label="Loading sessions..." compact />
                            </div>
                        )}
                        {hasSessions === true && (
                            <p className="text-[10px] text-primary/70 font-sans ml-1 uppercase tracking-wider">
                                Choose the time window used to explain rating changes.
                            </p>
                        )}
                    </div>

                    {/* Time Control Selector */}
                    <div className="flex flex-col gap-1.5">
                        <div className="flex bg-primary/5 rounded-sm p-1">
                            {(['bullet', 'blitz', 'rapid'] as const).map(tc => (
                                <button
                                    key={tc}
                                    type="button"
                                    onClick={() => setTimeControl(tc)}
                                    aria-pressed={timeControl === tc}
                                    className={`km-toggle-option km-focus-visible px-3 py-2 text-sm font-sans transition-all rounded-sm ${timeControl === tc ? 'km-toggle-selected bg-primary text-bg-primary shadow-sm' : 'text-primary/70'}`}
                                >
                                    {tc.charAt(0).toUpperCase() + tc.slice(1)}
                                </button>
                            ))}
                        </div>
                        {data && data.stats.games === 0 && (
                            <div className="text-[10px] text-primary/70 font-sans ml-1">
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
                            <p className="text-xs text-negative font-sans">
                                Could not record snapshot. Try again.
                            </p>
                        )}
                    </div>
                </div>
            </section>

            {error && (
                <DataStateError
                    message={error}
                    onRetry={() => fetchData()}
                    retryLabel="Retry"
                    ariaLabel="Retry loading rating insights"
                    compact
                />
            )}

            {loading && !data && (
                <DataStateLoading label="Analyzing games..." />
            )}

            {/* Latest Snapshot Confirmation Card */}
            {latestSnapshot && (
                <div className="p-4 border border-primary/10 bg-primary/5 rounded-sm max-w-md">
                    <h3 className="text-lg font-serif text-primary mb-1">Latest Snapshot</h3>
                    <p className="text-primary/80 font-sans text-sm">
                        {timeControl.charAt(0).toUpperCase() + timeControl.slice(1)} · {latestSnapshot.rating}
                    </p>
                    <p className="text-primary/70 font-sans text-xs">
                        Recorded just now
                    </p>
                    <p className="text-primary/70 font-sans text-sm mt-2">
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
                                <p className="text-primary/70 font-sans">See what influenced your rating changes over time.</p>
                            </div>

                            <div className="space-y-8">
                                <div className="space-y-2">
                                    <h3 className="font-serif text-lg text-primary">Step 1 — Record your first snapshot</h3>
                                    <p className="text-sm text-primary/70 font-sans leading-relaxed">
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
                                    <p className="text-sm text-primary/70 font-sans leading-relaxed">
                                        After you’ve played a few games, come back here to see what drove changes.
                                    </p>
                                </div>
                            </div>
                        </div>
                    )}

                    {/* STATE 1 & 2: Games exist */}
                    {hasGames && (
                        <>
                            {/* Window & Confidence Bar */}
                            <div className="flex flex-wrap items-center gap-3">
                                {windowDates && (
                                    <span className="text-xs font-sans text-primary/70">{windowDates}</span>
                                )}
                                <span className={`text-[10px] font-sans font-medium px-2 py-0.5 rounded-full ${confidenceBadge.color}`}>
                                    {confidenceBadge.label} ({N} games)
                                </span>
                                {data.rating.reference_rating > 0 && (
                                    <span className="text-xs font-sans text-primary/70">
                                        Ref: {data.rating.reference_rating}{data.rating.reference_is_approx ? ' (est.)' : ''}
                                    </span>
                                )}
                                {data.stats.missing_opponent_rating_games > 0 && (
                                    <span className="text-[10px] font-sans text-status-learning">
                                        {data.stats.missing_opponent_rating_games} game{data.stats.missing_opponent_rating_games > 1 ? 's' : ''} excluded (missing opponent rating)
                                    </span>
                                )}
                            </div>

                            {/* Rating Trend Chart */}
                            {chartData.length >= 2 && (
                                <section className="p-6 bg-primary/5 rounded-sm border border-primary/10">
                                    <h2 className="text-sm font-sans uppercase tracking-widest text-primary/70 mb-4">Rating Over Time</h2>
                                    <ResponsiveContainer width="100%" height={220}>
                                        <LineChart data={chartData}>
                                            <XAxis dataKey="label" tick={{ fontSize: 11 }} stroke="currentColor" strokeOpacity={0.2} />
                                            <YAxis domain={['dataMin - 20', 'dataMax + 20']} tick={{ fontSize: 11 }} stroke="currentColor" strokeOpacity={0.2} width={45} />
                                            <Tooltip
                                                contentStyle={{ fontSize: 12, borderRadius: 4, border: '1px solid rgba(0,0,0,0.1)' }}
                                                labelStyle={{ fontWeight: 600 }}
                                            />
                                            <Line
                                                type="monotone"
                                                dataKey="rating"
                                                stroke={chartTrendColor}
                                                strokeWidth={2}
                                                dot={false}
                                                activeDot={{ r: 4 }}
                                            />
                                            <ReferenceDot
                                                x={chartData[0].label}
                                                y={chartData[0].rating}
                                                r={4}
                                                fill="currentColor"
                                                fillOpacity={0.4}
                                            />
                                            <ReferenceDot
                                                x={chartData[chartData.length - 1].label}
                                                y={chartData[chartData.length - 1].rating}
                                                r={4}
                                                fill="currentColor"
                                                fillOpacity={0.8}
                                            />
                                        </LineChart>
                                    </ResponsiveContainer>
                                </section>
                            )}

                            {/* Summary Cards */}
                            <section className="grid grid-cols-1 md:grid-cols-4 gap-6">
                                <Card
                                    label="Net Change"
                                    value={hasSnapshots && data.rating.net_change !== null
                                        ? (data.rating.net_change > 0 ? `+${data.rating.net_change}` : `${data.rating.net_change}`)
                                        : "—"}
                                    sub={
                                        hasSnapshots && data.rating.start !== null && data.rating.end !== null
                                            ? `${data.rating.start} → ${data.rating.end}`
                                            : windowLabel
                                    }
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
                                    value={hasGames && data.stats.actual_minus_expected !== null
                                        ? (data.stats.actual_minus_expected > 0 ? `+${(data.stats.actual_minus_expected || 0).toFixed(1)}` : `${(data.stats.actual_minus_expected || 0).toFixed(1)}`)
                                        : "—"}
                                    sub="Actual minus expected score"
                                    helper="Positive means you outperformed expectations. Negative means you underperformed."
                                />
                                <Card
                                    label="Opponent Strength"
                                    value={hasGames ? (data.stats.avg_opponent_rating?.toString() || "—") : "—"}
                                    sub={data.rating.reference_rating > 0
                                        ? `Avg opponent vs your ${data.rating.reference_rating}`
                                        : "Average opponent rating"
                                    }
                                />
                            </section>

                            {/* Drivers */}
                            <section className="border-t border-primary/10 pt-8">
                                <h2 className="text-2xl font-serif text-primary mb-6">Primary Drivers</h2>
                                {data.drivers.length > 0 ? (
                                    <ul className="space-y-4">
                                        {data.drivers.map((driver, i) => {
                                            const dotColor = driver.direction === 'up' ? 'bg-emerald-500' : driver.direction === 'down' ? 'bg-red-500' : 'bg-primary/40';
                                            const severityLabel = driver.severity === 'major' ? 'Major' : driver.severity === 'moderate' ? 'Moderate' : 'Minor';
                                            const severityColor = driver.severity === 'major' ? 'bg-primary/10 text-primary/70' : driver.severity === 'moderate' ? 'bg-primary/5 text-primary/70' : 'bg-primary/5 text-primary/70';
                                            return (
                                                <li key={i} className="flex items-start gap-3 text-lg font-sans text-primary/80">
                                                    <span className={`mt-2 w-2 h-2 rounded-full shrink-0 ${dotColor}`} />
                                                    <span>{driver.text}</span>
                                                    <span className={`text-[10px] font-medium px-1.5 py-0.5 rounded-full shrink-0 mt-1 ${severityColor}`}>{severityLabel}</span>
                                                </li>
                                            );
                                        })}
                                    </ul>
                                ) : (
                                    <p className="text-primary/70 font-sans italic">
                                        Performance matched expectations — no standout drivers in this window.
                                    </p>
                                )}
                                <p className="mt-8 text-xs text-primary/70 font-sans italic">
                                    Based on results vs Elo expectation. Chess.com uses internal factors, so this is directional only.
                                </p>
                            </section>

                            {/* Highlights */}
                            <section className="border-t border-primary/10 pt-8">
                                {(data.highlights.worst_surprises.length > 0 || data.highlights.best_surprises.length > 0) ? (
                                    <div className="grid grid-cols-1 md:grid-cols-2 gap-12">
                                        {data.highlights.worst_surprises.length > 0 && (
                                            <div>
                                                <h3 className="text-xl font-serif text-primary mb-6 flex items-center gap-2">
                                                    <span>Most Costly Games</span>
                                                    <span className="text-xs font-sans bg-negative-soft text-negative px-2 py-1 rounded-full">Negative Surprise</span>
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
                                                    <span className="text-xs font-sans bg-positive-soft text-positive px-2 py-1 rounded-full">Positive Surprise</span>
                                                </h3>
                                                <div className="space-y-2">
                                                    {data.highlights.best_surprises.map(game => (
                                                        <GameRow key={game.game_id} game={game} type="good" />
                                                    ))}
                                                </div>
                                            </div>
                                        )}
                                    </div>
                                ) : (
                                    <div>
                                        <h3 className="text-xl font-serif text-primary mb-3">Game Highlights</h3>
                                        <p className="text-primary/70 font-sans italic">
                                            All games matched expectations — no significant surprises in this window.
                                        </p>
                                    </div>
                                )}
                            </section>
                        </>
                    )}
                </>
            )}
        </div>
    );
}

const Card = ({ label, value, sub, helper, highlight, positive, extra }: { label: string, value: string, sub?: string, helper?: string, highlight?: boolean, positive?: boolean, extra?: string }) => (
    <div className="p-6 bg-primary/5 rounded-sm border border-primary/10">
        <div className="text-xs font-sans uppercase tracking-widest text-primary/70 mb-2">{label}</div>
        <div className={`text-3xl font-serif mb-1 ${highlight ? (positive ? 'text-positive' : 'text-negative') : 'text-primary'}`}>
            {value}
        </div>
        {sub && <div className="text-xs font-sans text-primary/70 mb-1">{sub}</div>}
        {helper && <div className="text-xs font-sans text-primary/70 italic">{helper}</div>}
        {extra && <div className="text-xs font-sans text-primary/70 mt-2">{extra}</div>}
    </div>
);

const GameRow = ({ game, type }: { game: HighlightGame, type: 'good' | 'bad' }) => {
    const playedDate = game.played_at
        ? new Date(game.played_at).toLocaleDateString(LOCALE, { month: 'short', day: 'numeric' })
        : null;
    return (
        <a href={game.url} target="_blank" rel="noopener noreferrer" className="block p-4 hover:bg-primary/5 transition-colors border-b border-primary/5 group">
            <div className="flex justify-between items-center mb-1">
                <div className="font-medium text-primary/80 group-hover:text-primary transition-colors">
                    vs {game.opponent_rating}
                    {playedDate && <span className="text-xs font-normal text-primary/70 ml-2">{playedDate}</span>}
                </div>
                <div className={`text-sm font-bold ${type === 'good' ? 'text-positive' : 'text-negative'}`}>
                    {game.result}
                </div>
            </div>
            <div className="flex justify-between items-center text-xs text-primary/70 font-sans">
                <div>Expected: {game.expected_score.toFixed(2)}</div>
                <div>{(Math.abs(game.rating_diff || 0))} pts {(game.rating_diff || 0) > 0 ? 'higher' : 'lower'}</div>
            </div>
        </a>
    );
};
