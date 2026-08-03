import { useEffect, useState } from 'react';
import { getRatingHistory, type SnapshotHistoryItem } from '../api/ratings';
import { Sparkline } from './Sparkline';
import { TC_LABEL, formatSigned, type TimeControl } from '../utils/ratings';

const CONTROLS: TimeControl[] = ['bullet', 'blitz', 'rapid'];

interface TimeControlOverviewProps {
    username: string;
    active: TimeControl;
    onSelect: (tc: TimeControl) => void;
}

/**
 * All three time controls at a glance — current rating, recent trajectory
 * sparkline, and net movement — instead of a blind Bullet/Blitz/Rapid toggle.
 * Each tile is the switcher for its control. Built from the lightweight
 * snapshot-history endpoint (recorded automatically on import/session/visits),
 * not three heavyweight explain analyses; the deep dive below stays scoped to
 * the active control.
 */
export function TimeControlOverview({ username, active, onSelect }: TimeControlOverviewProps) {
    // Per-control history: an array (possibly empty) when the fetch succeeded,
    // null when it FAILED — "no games yet" and "couldn't load" must not be
    // conflated, or an outage reads as an empty account.
    const [histories, setHistories] = useState<Record<TimeControl, SnapshotHistoryItem[] | null> | null>(null);

    // Reset to the loading state when the username changes (render-phase state
    // sync, matching the codebase pattern — sync setState in effects is linted).
    const [prevUsername, setPrevUsername] = useState(username);
    if (username !== prevUsername) {
        setPrevUsername(username);
        setHistories(null);
    }

    useEffect(() => {
        let cancelled = false;
        Promise.allSettled(CONTROLS.map((tc) => getRatingHistory(username, tc, 20))).then((results) => {
            if (cancelled) return;
            const next = { bullet: null, blitz: null, rapid: null } as Record<TimeControl, SnapshotHistoryItem[] | null>;
            results.forEach((r, i) => {
                next[CONTROLS[i]] = r.status === 'fulfilled' ? r.value : null;
            });
            setHistories(next);
        });
        return () => { cancelled = true; };
    }, [username]);

    return (
        <section aria-label="Rating by time control" className="grid grid-cols-1 sm:grid-cols-3 gap-4">
            {CONTROLS.map((tc) => {
                const history = histories?.[tc];
                const isActive = tc === active;
                const points = (history ?? []).map((h) => h.rating);
                const latest = points.length > 0 ? points[points.length - 1] : null;
                const delta = points.length >= 2 ? points[points.length - 1] - points[0] : null;
                return (
                    <button
                        key={tc}
                        type="button"
                        onClick={() => onSelect(tc)}
                        aria-pressed={isActive}
                        // Without this the accessible name is built by
                        // concatenating the child text nodes, which runs the
                        // label, the delta and the rating together as one
                        // unbroken string — "Bullet+661486". Spelling it out
                        // also says what the delta measures, which the visible
                        // tile only conveys through a hover title.
                        aria-label={
                            latest !== null
                                ? `${TC_LABEL[tc]}: ${latest}` +
                                  (delta !== null && delta !== 0
                                      ? `, ${formatSigned(delta)} across your last ${points.length} snapshots`
                                      : '')
                                // Mirrors the visible status word for word. The
                                // two empty cases are different facts — a null
                                // history is a *failed request*, an empty one is
                                // a control never played — and collapsing them
                                // told a screen-reader user their data does not
                                // exist when it merely failed to load. Reusing
                                // the visible wording also keeps the accessible
                                // name a superset of what is on screen, which is
                                // what voice control matches against.
                                : history === null
                                    ? `${TC_LABEL[tc]}: unavailable`
                                    : `${TC_LABEL[tc]}: no games yet`
                        }
                        className={`text-left p-4 rounded-sm border transition-all km-interactive km-focus-visible ${
                            isActive
                                ? 'bg-primary/10 border-primary/40'
                                : 'bg-primary/5 border-primary/10 hover:border-primary/30'
                        }`}
                    >
                        <div className="flex items-baseline justify-between gap-2">
                            <span className="text-xs font-sans uppercase tracking-widest text-primary/70">
                                {TC_LABEL[tc]}
                            </span>
                            {delta !== null && delta !== 0 && (
                                <span
                                    className={`text-xs font-mono ${delta > 0 ? 'text-positive' : 'text-negative'}`}
                                    // The windowed "Net Change" card below measures a different
                                    // span — say what this number covers so the two can't read
                                    // as a contradiction.
                                    title={`Across your last ${points.length} recorded snapshots`}
                                >
                                    {formatSigned(delta)}
                                </span>
                            )}
                        </div>
                        <div className="mt-1 flex items-end justify-between gap-3">
                            {histories === null ? (
                                <div className="h-8 w-16 bg-primary/10 rounded-sm animate-pulse" aria-hidden="true" />
                            ) : (
                                <span className="text-2xl font-serif text-primary leading-none">
                                    {latest ?? '—'}
                                </span>
                            )}
                            {points.length >= 2 && (
                                <Sparkline
                                    points={points}
                                    trend={delta !== null && delta < 0 ? 'down' : 'up'}
                                    width={84}
                                    height={24}
                                />
                            )}
                        </div>
                        {histories !== null && latest === null && (
                            <p className="mt-1 text-[10px] font-sans text-primary/70">
                                {history === null ? 'Unavailable' : 'No games yet'}
                            </p>
                        )}
                    </button>
                );
            })}
        </section>
    );
}
