import { LOCALE } from '../utils/locale';
import { useEffect, useState, useCallback, useMemo, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import { LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer, ReferenceDot } from 'recharts';
import { useChessUsername } from '../context/ChessUsernameContext';
import { getRatingExplain, getRatingHistory, type ExplainResponse, type HighlightGame, type SnapshotHistoryItem } from '../api/ratings';
import { getRecentSessions } from '../api/sessions';
import { PageHeader } from '../components/PageHeader';
import { StatCard } from '../components/StatCard';
import { ConfidenceBadge } from '../components/ConfidenceBadge';
import { TC_LABEL, formatSigned } from '../utils/ratings';
import { DataStateError, DataStateLoading, DataStateOffline, DataStateSkeleton } from '../components/DataState';
import { useOnlineStatus } from '../hooks/useOnlineStatus';
import { useLatestRequest } from '../hooks/useLatestRequest';

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
    // Ref, not state: it's only read inside fetchData. Keeping it as state
    // changed fetchData's identity after the sessions probe resolved, which
    // re-ran the coordinated effect and double-fetched everything on load.
    const lastSessionIdRef = useRef<string | null>(null);
    // The in-flight (or settled) sessions probe, keyed by username. Sessions
    // don't depend on time control or window, so the probe fires once per
    // username; the coordinated explain effect awaits this promise instead of
    // re-probing when its other deps change. The promise never rejects —
    // failures resolve to null (treated as "no sessions").
    const sessionsProbeRef = useRef<{ username: string; promise: Promise<string | null> } | null>(null);
    const [sessionsLoading, setSessionsLoading] = useState(false);
    // null = not loaded yet. The distinction matters: empty-state branches
    // (first-import onboarding vs thin-window note) must not render from a
    // "no snapshots" reading that is really just "history hasn't arrived".
    const [history, setHistory] = useState<SnapshotHistoryItem[] | null>(null);
    // History has its own error slot: explain's setError(null) must not be able
    // to swallow a history failure (and vice versa) now that they fetch apart.
    const [historyError, setHistoryError] = useState<string | null>(null);
    const online = useOnlineStatus();
    // Explain and history fetch independently, so each needs its own
    // stale-response guard — sharing one generation counter would make
    // either fetch mark the other stale.
    const explainRequest = useLatestRequest();
    const historyRequest = useLatestRequest();

    const fetchExplain = useCallback(async (resolvedSessionId?: string | null) => {
        if (!username) return;
        const effectiveSessionId = resolvedSessionId !== undefined ? resolvedSessionId : lastSessionIdRef.current;
        // 'session' mode with no session id yet (e.g. Retry clicked while the
        // sessions probe is still in flight): an unwindowed request would
        // silently violate the selected mode. Skip — the coordinated effect
        // fires explain once the probe resolves.
        if (windowSource === 'session' && !effectiveSessionId) return;
        // Guard against stale-response races: a username/time-control/window change
        // begins a newer request; the older, slower response must not clobber it.
        const token = explainRequest.begin();
        setLoading(true);
        setError(null);
        try {
            let sinceStr: string | undefined = undefined;
            let sessionId: string | undefined = undefined;

            if (windowSource === 'fallback_7d') {
                const d = new Date();
                d.setDate(d.getDate() - 7);
                sinceStr = d.toISOString();
            } else if (windowSource === 'session' && effectiveSessionId) {
                sessionId = effectiveSessionId;
            }

            const resp = await getRatingExplain(username, timeControl, sessionId, sinceStr);
            if (token.isStale()) return;
            setData(resp);
        } catch (err) {
            if (token.isStale()) return;
            setError(err instanceof Error ? err.message : 'Failed to load insights');
        } finally {
            if (!token.isStale()) setLoading(false);
        }
    }, [username, timeControl, windowSource, explainRequest]);

    const fetchHistory = useCallback(async () => {
        if (!username) return;
        const token = historyRequest.begin();
        setHistoryError(null);
        try {
            const historyData = await getRatingHistory(username, timeControl);
            if (token.isStale()) return;
            setHistory(historyData);
        } catch (err) {
            if (token.isStale()) return;
            setHistoryError(err instanceof Error ? err.message : 'Failed to load rating history');
        }
    }, [username, timeControl, historyRequest]);

    // Retry affordances refresh both requests.
    const refetch = useCallback(() => {
        fetchExplain();
        fetchHistory();
    }, [fetchExplain, fetchHistory]);

    // Explain data from the previous username/timeControl must not keep
    // rendering under the new selection's labels (the username can change
    // in-place via the global editor — no remount). History gets the same
    // treatment in its own effect below. windowSource toggles deliberately
    // keep the old window's data visible while the new window loads.
    useEffect(() => {
        setData(null);
        setError(null);
    }, [username, timeControl]);

    // History depends only on username + time control — its params don't include
    // the window — so it fetches on its own, concurrently with the sessions probe,
    // and is NOT refetched when windowSource toggles.
    useEffect(() => {
        if (!username) return;
        // The current list belongs to the previous username/timeControl pair;
        // pairing it with fresher explain data would chart the wrong snapshots
        // under the new labels. Reset to "unknown" until the new fetch lands.
        setHistory(null);
        fetchHistory();
    }, [username, fetchHistory]);

    // Sessions probe: keyed on username ONLY. Time-control and window changes
    // re-run the coordination effect below (via fetchExplain's identity), and
    // before this split each re-run probed getRecentSessions again — a wasted
    // request per toggle. Now the probe fires once per username and caches its
    // promise in sessionsProbeRef for the coordination effect to await.
    // Declared before that effect so the ref is populated when it runs.
    useEffect(() => {
        if (!username) return;
        let cancelled = false;
        // The cached ID belongs to the previous username — a Retry clicked
        // before this probe resolves must not window explain by it.
        lastSessionIdRef.current = null;
        setSessionsLoading(true);
        sessionsProbeRef.current = {
            username,
            promise: (async () => {
                let resolvedSessionId: string | null = null;
                try {
                    const sessions = await getRecentSessions(username, 1);
                    resolvedSessionId = sessions.length > 0 ? sessions[0].session_id : null;
                } catch {
                    resolvedSessionId = null; // probe failure reads as "no sessions"
                }
                // The promise result stays valid for late awaiters; only the
                // state writes are gated on this run still being current.
                if (!cancelled) {
                    setHasSessions(resolvedSessionId !== null);
                    lastSessionIdRef.current = resolvedSessionId;
                    setSessionsLoading(false);
                }
                return resolvedSessionId;
            })(),
        };
        return () => { cancelled = true; };
    }, [username]);

    // Coordination: explain needs the session ID only in 'session' mode, so only
    // that mode serializes explain behind the sessions probe — and re-runs await
    // the probe's cached (usually already-settled) promise, so no request fires.
    // In fallback_7d mode explain starts immediately. When 'session' mode resolves
    // to zero sessions, auto-switch to "Last 7 Days": the windowSource write
    // re-runs this effect, whose fallback branch then fires the explain.
    useEffect(() => {
        if (!username) return;
        let cancelled = false;

        async function coordinateExplain() {
            if (windowSource === 'fallback_7d') {
                // Date-windowed — no session ID needed. Its token guards staleness.
                fetchExplain(null);
                return;
            }
            const probe = sessionsProbeRef.current;
            // Unreachable in practice: the probe effect above runs first in the
            // same commit for any username. Bail rather than fetch unwindowed.
            if (!probe || probe.username !== username) return;
            const resolvedSessionId = await probe.promise;
            if (cancelled) return;
            if (resolvedSessionId === null) {
                setWindowSource('fallback_7d');
                return;
            }
            fetchExplain(resolvedSessionId);
        }

        coordinateExplain();
        return () => { cancelled = true; };
    }, [username, windowSource, fetchExplain, setWindowSource]);

    // Chart source: render the server-fused series when present — the backend
    // decides how per-game Elo and snapshot anchors combine, so the line's
    // endpoints always match the Net Change card. Older payloads without
    // chart_series fall back to the raw trajectory, then to recorded snapshot
    // history when the window has no games.
    // Labels are de-duplicated so same-day points keep unique X-axis keys.
    const chart = useMemo(() => {
        const fused = data?.chart_series ?? [];
        const trajectory = data?.trajectory ?? [];
        // Contract checks: a new payload fuses at least the trajectory into
        // chart_series, and its endpoints match rating.start/end. A violation
        // silently re-introduces the card/chart mismatch this series exists
        // to prevent — warn so the regression is visible, but keep rendering.
        if (data?.chart_series !== undefined && fused.length < 2 && trajectory.length >= 2) {
            console.warn('[ratings] chart_series shorter than trajectory; falling back to legacy chart source', { chartSeries: fused.length, trajectory: trajectory.length });
        }
        const useFused = fused.length >= 2;
        if (useFused && data
            && ((data.rating.start !== null && fused[0].rating !== data.rating.start)
                || (data.rating.end !== null && fused[fused.length - 1].rating !== data.rating.end))) {
            console.warn('[ratings] chart_series endpoints diverge from rating anchors', {
                seriesStart: fused[0].rating, seriesEnd: fused[fused.length - 1].rating,
                ratingStart: data.rating.start, ratingEnd: data.rating.end,
            });
        }
        const source: 'games' | 'snapshots' | 'mixed' = useFused
            ? (fused.some(p => p.source === 'snapshot') ? 'mixed' : 'games')
            : trajectory.length >= 2 ? 'games' : 'snapshots';
        const raw = useFused
            ? fused.map(p => ({ at: p.at, rating: p.rating }))
            : source === 'games'
                ? trajectory.map(p => ({ at: p.played_at, rating: p.rating }))
                : (history ?? []).map(h => ({ at: h.recorded_at, rating: h.rating }));
        const dateCounts = new Map<string, number>();
        const points = raw.map(p => {
            const base = new Date(p.at).toLocaleDateString(LOCALE, { month: 'short', day: 'numeric' });
            const count = (dateCounts.get(base) ?? 0) + 1;
            dateCounts.set(base, count);
            return { label: count > 1 ? `${base} (${count})` : base, rating: p.rating };
        });
        return { source, points };
    }, [data, history]);

    const chartData = chart.points;
    // Direction only — the actual colors are theme tokens applied via the
    // km-trend-* CSS classes (SVG attributes can't consume var()).
    const chartTrend: 'up' | 'down' = chartData.length < 2
        || chartData[chartData.length - 1].rating >= chartData[0].rating
        ? 'up'
        : 'down';

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

    // Base "has snapshots" on the ACTUAL recorded snapshot history, not on the windowed
    // explain payload. A window with no in/pre-window anchor (or no games) returns
    // rating.end === null even when the user has snapshots on file — deriving from
    // rating.end mislabels that as a brand-new user and shows first-snapshot onboarding.
    const hasSnapshots = (history?.length ?? 0) > 0;
    // History fetches independently now — until it has loaded (or failed), the
    // empty-state branches below must not assume "no snapshots".
    const historyKnown = history !== null;
    const hasGames = (data?.stats.games || 0) > 0;
    // Whether THIS window has both anchors needed to show a net rating change. This is
    // a property of the explain payload, distinct from whether snapshots exist at all.
    const hasWindowRating = data?.rating.end != null;
    // First-snapshot onboarding only when there is genuinely nothing for this control:
    // no recorded snapshots AND no games in the window.
    const isState0 = data && historyKnown && !hasSnapshots && !hasGames;
    // Snapshots exist but this window has no games to explain a rating change. Show the
    // recorded history + an honest note instead of the first-snapshot onboarding.
    const thinWindow = data != null && hasSnapshots && !hasGames;
    // The server flagged the in-window sample as too small to draw conclusions from,
    // even though there are some games. Surface an honest note alongside the drivers.
    const insufficientSample = data?.insufficient_data === true && hasGames;

    const N = data?.stats.games ?? 0;
    const casualExcluded = data?.stats.casual_games_excluded ?? 0;
    // Prefer the server's canonical confidence (computed from rated games with a
    // known opponent rating). Fall back to the game count only for older payloads.
    const confidence = data?.confidence
        ?? (N < LOW_CONFIDENCE_THRESHOLD ? 'low' : N < HIGH_CONFIDENCE_THRESHOLD ? 'medium' : 'high');
    const timeControlLabel = TC_LABEL[timeControl];
    const windowLabel = `${N} rated ${timeControlLabel} game${N === 1 ? '' : 's'} in window`;

    const formatDate = (iso: string) => {
        const d = new Date(iso);
        return d.toLocaleDateString(LOCALE, { month: 'short', day: 'numeric', year: 'numeric' });
    };
    const windowDates = data?.window
        ? `${formatDate(data.window.start)} – ${formatDate(data.window.end)}`
        : undefined;

    // Per-anchor estimated flags. Older payloads only send the conflated
    // is_estimated boolean; apply it to both anchors in that case.
    const startEstimated = data?.rating.start_is_estimated ?? data?.rating.is_estimated ?? false;
    const endEstimated = data?.rating.end_is_estimated ?? data?.rating.is_estimated ?? false;
    const estimatedNote = startEstimated && endEstimated
        ? ' (est. from games)'
        : startEstimated
        ? ' (start est. from games)'
        : endEstimated
        ? ' (end est. from games)'
        : '';


    return (
        <div className="space-y-12 animate-teedin pb-20">
            <section className="flex flex-col md:flex-row justify-between items-end gap-6">
                <PageHeader
                    title="Rating Insights"
                    subtitle="What moved your rating — tracked automatically from your games."
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
                                {/* Thin window still charts snapshots below, so "no data yet"
                                    would contradict the visible chart — scope the copy. */}
                                <span>
                                    {thinWindow
                                        ? `No ${timeControlLabel} games in this window.`
                                        : `No data yet for ${timeControlLabel}.`}
                                </span>
                            </div>
                        )}
                    </div>

                </div>
            </section>

            {/* Explain failing always gets a banner. A history failure only matters
                when there are no games — the games view charts from the explain
                payload (chart_series/trajectory), not history, so a history blip
                there must not paint an error over a fully-working view. Retry
                refetches both, so every surfaced failure recovers. */}
            {(error || (historyError && !hasGames)) && (
                !online ? (
                    // A failed load while the browser is offline is a connectivity
                    // problem, not a server error — say so instead of a bare message.
                    <DataStateOffline onRetry={refetch} compact />
                ) : (
                    <DataStateError
                        message={error ?? historyError ?? ''}
                        onRetry={refetch}
                        retryLabel="Retry"
                        ariaLabel="Retry loading rating insights"
                        compact
                    />
                )
            )}

            {/* Cover BOTH fetch phases (sessions probe, then explain/history):
                gating on `loading` alone left the main area blank for the whole
                sessions request on slow connections — visibly broken. Skeletons
                mirror the loaded layout (metadata line, chart, stat cards). */}
            {(loading || sessionsLoading) && !data && !error && !historyError && (
                <DataStateSkeleton label="Analyzing games..." className="space-y-8">
                    <div className="h-4 w-80 max-w-full bg-primary/5 rounded-sm animate-pulse" />
                    <div className="h-[284px] bg-primary/5 border border-primary/10 rounded-sm animate-pulse" />
                    <div className="grid grid-cols-1 md:grid-cols-4 gap-6">
                        {[1, 2, 3, 4].map(i => (
                            <div key={i} className="h-40 bg-primary/5 border border-primary/10 rounded-sm animate-pulse" />
                        ))}
                    </div>
                </DataStateSkeleton>
            )}

            {data && (
                <>
                    {/* History still in flight for a 0-games window: neither empty
                        state can be told apart yet, so hold a placeholder instead of
                        flashing the wrong onboarding (or a blank area). */}
                    {!hasGames && !historyKnown && !historyError && (
                        <div className="h-[284px] bg-primary/5 border border-primary/10 rounded-sm animate-pulse" role="status">
                            <span className="sr-only">Loading rating history...</span>
                        </div>
                    )}

                    {/* STATE 0: no games and no rating history for this control.
                        Snapshots are recorded automatically (on import, session
                        completion, and visits here), so the only ask is games. */}
                    {isState0 && (
                        <div className="max-w-xl mx-auto bg-primary/5 border border-primary/10 p-12 rounded-sm space-y-8">
                            <div>
                                <h2 className="text-2xl font-serif text-primary mb-2">No {timeControlLabel} games yet</h2>
                                <p className="text-primary/70 font-sans leading-relaxed">
                                    Import your Chess.com games — or play a few {timeControlLabel} games and
                                    import again — and this page will chart your rating and explain what moved it.
                                </p>
                            </div>
                            <div className="space-y-3">
                                <button
                                    type="button"
                                    onClick={() => navigate('/')}
                                    className="px-6 py-2 bg-primary text-bg-primary hover:opacity-90 rounded-sm font-serif transition-colors km-focus-visible km-interactive"
                                >
                                    Import your games
                                </button>
                                <p className="text-xs text-primary/70 font-sans leading-relaxed">
                                    Your rating is tracked automatically — when you import games, finish a
                                    training session, or visit this page. Nothing to record by hand.
                                </p>
                            </div>
                        </div>
                    )}

                    {/* THIN WINDOW: snapshots exist, but this window has no games (or an
                        insufficient sample) to explain a rating change. Show the recorded
                        history + an honest note — never the first-snapshot onboarding. */}
                    {thinWindow && (
                        <div className="space-y-6">
                            {chartData.length >= 2 && (
                                <RatingChart chartData={chartData} trend={chartTrend} source={chart.source} />
                            )}
                            <div className="max-w-xl bg-primary/5 border border-primary/10 p-6 rounded-sm">
                                <h3 className="font-serif text-lg text-primary mb-1">Not enough games in this window</h3>
                                <p className="text-sm text-primary/70 font-sans leading-relaxed">
                                    You have recorded snapshots for {timeControlLabel}, but there aren’t enough
                                    {' '}{timeControlLabel} games in this window to explain your rating change.
                                    Widen the window or play a few games, then check back.
                                </p>
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
                                <ConfidenceBadge confidence={confidence} games={N} />
                                {data.rating.reference_rating > 0 && (
                                    <span className="text-xs font-sans text-primary/70">
                                        Ref: {data.rating.reference_rating}{data.rating.reference_is_approx ? ' (est.)' : ''}
                                    </span>
                                )}
                                {insufficientSample && (
                                    <span className="text-[10px] font-sans text-status-learning">
                                        Small sample — not enough games in this window to explain the rating change.
                                    </span>
                                )}
                                {data.stats.missing_opponent_rating_games > 0 && (
                                    <span className="text-[10px] font-sans text-status-learning">
                                        {data.stats.missing_opponent_rating_games} game{data.stats.missing_opponent_rating_games > 1 ? 's' : ''} excluded (missing opponent rating)
                                    </span>
                                )}
                                {casualExcluded > 0 && (
                                    <span className="text-[10px] font-sans text-primary/70">
                                        {casualExcluded} casual game{casualExcluded > 1 ? 's' : ''} excluded (don’t affect rating)
                                    </span>
                                )}
                            </div>

                            {/* Rating Trend Chart */}
                            {chartData.length >= 2 && (
                                <RatingChart chartData={chartData} trend={chartTrend} source={chart.source} />
                            )}

                            {/* Summary Cards */}
                            <section className="grid grid-cols-1 md:grid-cols-4 gap-6">
                                <StatCard
                                    label="Net Change"
                                    value={hasWindowRating && data.rating.net_change !== null
                                        ? formatSigned(data.rating.net_change)
                                        : "—"}
                                    sub={
                                        hasWindowRating && data.rating.start !== null && data.rating.end !== null
                                            ? `${data.rating.start} → ${data.rating.end}${estimatedNote}`
                                            : windowLabel
                                    }
                                    highlight={hasWindowRating && data.rating.net_change !== null && data.rating.net_change !== 0}
                                    positive={hasWindowRating && data.rating.net_change !== null && data.rating.net_change > 0}
                                    extra={!hasSnapshots && !hasWindowRating ? "Rating is tracked automatically as you play and import games." : undefined}
                                />
                                <StatCard
                                    label="Performance"
                                    value={hasGames
                                        ? `${data.stats.wins}W - ${data.stats.draws}D - ${data.stats.losses}L`
                                        : "—"}
                                    sub={`${data.stats.games} games analyzed`}
                                />
                                <StatCard
                                    label="Performance vs Expectation"
                                    value={hasGames && data.stats.actual_minus_expected !== null
                                        ? formatSigned(data.stats.actual_minus_expected, 1)
                                        : "—"}
                                    sub="Actual minus expected score"
                                    helper="Positive means you outperformed expectations. Negative means you underperformed."
                                />
                                <StatCard
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
                                            const severityColor = driver.severity === 'major' ? 'bg-primary/10 text-primary/70' : 'bg-primary/5 text-primary/70';
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

const RatingChart = ({ chartData, trend, source }: { chartData: { label: string; rating: number }[], trend: 'up' | 'down', source: 'games' | 'snapshots' | 'mixed' }) => (
    // div, not section: role="img" is not an allowed role on section (axe aria-allowed-role)
    <div
        className="p-6 bg-primary/5 rounded-sm border border-primary/10"
        role="img"
        aria-label={`Rating over time, ${chartData.length} points from ${chartData[0].rating} to ${chartData[chartData.length - 1].rating}`}
    >
        <div className="flex items-baseline justify-between gap-3 mb-4">
            <h2 className="text-sm font-sans uppercase tracking-widest text-primary/70">Rating Over Time</h2>
            <span className="text-[10px] font-sans text-primary/70">
                {source === 'games'
                    ? 'From your games in this window'
                    : source === 'mixed'
                    ? 'From your games and rating snapshots'
                    : 'From recorded snapshots'}
            </span>
        </div>
        <ResponsiveContainer width="100%" height={220}>
            <LineChart data={chartData}>
                <XAxis dataKey="label" tick={{ fontSize: 11 }} stroke="currentColor" strokeOpacity={0.2} />
                <YAxis domain={['dataMin - 20', 'dataMax + 20']} tick={{ fontSize: 11 }} stroke="currentColor" strokeOpacity={0.2} width={45} />
                {/* Theme via the runtime --bg-primary/--text-primary vars: the
                    --color-* aliases live in @theme inline, so they are never
                    emitted as real CSS vars and would resolve to nothing here. */}
                <Tooltip
                    contentStyle={{
                        fontSize: 12,
                        borderRadius: 4,
                        border: '1px solid color-mix(in srgb, var(--text-primary) 20%, transparent)',
                        backgroundColor: 'var(--bg-primary)',
                        color: 'var(--text-primary)',
                    }}
                    itemStyle={{ color: 'var(--text-primary)' }}
                    labelStyle={{ fontWeight: 600, color: 'var(--text-primary)' }}
                />
                <Line
                    type="monotone"
                    dataKey="rating"
                    name="Rating"
                    className={`km-trend-${trend}`}
                    stroke="currentColor"
                    strokeWidth={2}
                    dot={false}
                    activeDot={{ r: 4 }}
                    // No draw-in animation: respects the app's reduced-motion
                    // stance, and the dasharray draw-in can stall invisible in
                    // background tabs until a re-render.
                    isAnimationActive={false}
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
    </div>
);


const GameRow = ({ game, type }: { game: HighlightGame, type: 'good' | 'bad' }) => {
    const playedDate = game.played_at
        ? new Date(game.played_at).toLocaleDateString(LOCALE, { month: 'short', day: 'numeric' })
        : null;
    const diff = game.rating_diff ?? 0;
    const diffLabel = diff === 0 ? 'same rating' : `${Math.abs(diff)} pts ${diff > 0 ? 'higher' : 'lower'}`;
    return (
        <a href={game.url} target="_blank" rel="noopener noreferrer" className="block p-4 hover:bg-primary/5 transition-colors border-b border-primary/5 group">
            <div className="flex justify-between items-center mb-1">
                <div className="font-medium text-primary/80 group-hover:text-primary transition-colors">
                    vs {game.opponent_username ?? 'opponent'}
                    {game.opponent_rating !== null && <span className="text-primary/70 font-normal"> ({game.opponent_rating})</span>}
                    {playedDate && <span className="text-xs font-normal text-primary/70 ml-2">{playedDate}</span>}
                </div>
                <div className={`text-sm font-bold ${type === 'good' ? 'text-positive' : 'text-negative'}`}>
                    {game.result}
                </div>
            </div>
            <div className="flex justify-between items-center text-xs text-primary/70 font-sans">
                <div>Expected: {game.expected_score.toFixed(2)}</div>
                <div>{diffLabel}</div>
            </div>
        </a>
    );
};
