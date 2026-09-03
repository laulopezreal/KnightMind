import { Link } from 'react-router-dom';
import type { TodaysFocusResponse } from '../api/users';

interface TodaysFocusCardProps {
    data: TodaysFocusResponse;
}

/**
 * The one habit worth working on today, and why.
 *
 * Every other card on this page describes; this one recommends. That is a
 * stronger claim, so it carries a stricter rule: it recommends only what the
 * data supports, and says nothing when nothing qualifies.
 *
 * Three states, all honest:
 *
 * - **A focus.** Named, described, and accompanied by the numbers it rests on,
 *   so the user can disagree with it on evidence rather than take it on faith.
 * - **Not yet.** Some causes exist but none has recurred enough to be a
 *   tendency. Distinct from having nothing at all — one is "keep playing", the
 *   other is "import some games".
 * - **Nothing.** The card renders nothing rather than an empty shell.
 *
 * The ordinary call to action opens a *biased* due session rather than a
 * filtered one. When nothing is due, only the server may offer a separate
 * focus-practice session, whose snapshot keeps future-position practice from
 * changing ordinary scheduling.
 */
export function TodaysFocusCard({ data }: TodaysFocusCardProps) {
    const { focus, below_threshold, pending } = data;

    if (!focus) {
        if (below_threshold === 0 && pending === 0) {
            return null;
        }
        return (
            <Shell>
                <p className="font-sans text-sm text-primary/70">
                    {pending > 0
                        ? 'Still analysing your mistakes — a focus appears once a habit recurs.'
                        : 'No habit has recurred often enough to build a plan on yet. Keep playing — this fills in on its own.'}
                </p>
            </Shell>
        );
    }

    return (
        <Shell>
            <p className="font-serif text-2xl text-primary">{focus.name}</p>
            <p className="font-sans text-sm text-primary/80 mt-2 leading-relaxed">
                {focus.description}
            </p>

            {/* The evidence, not a score. A bare priority number would be
                unfalsifiable; these are the figures it was computed from. */}
            <p className="font-sans text-xs text-primary/70 mt-3">
                Why this: {focus.rationale}
            </p>

            {/* "N ready", not "train N puzzles": a session is a fixed size and
                will top itself up from the rest of the due queue, so promising
                a session of exactly N would be a promise it does not keep. The
                count is still real — it is how many of this pattern could be
                served right now — and each puzzle says why it was included. */}
            {focus.trainable_now && focus.trainable_now > 0 ? (
                <Link
                    to={`/puzzles?focus_cause=${encodeURIComponent(focus.cause)}`}
                    className="km-interactive km-focus-visible inline-flex items-center mt-4 min-h-11 border border-primary/40 rounded-sm px-4 py-2 font-sans text-sm text-primary hover:bg-primary/10 transition-colors"
                >
                    Train this pattern · {focus.trainable_now} ready
                </Link>
            ) : focus.practice_available && (focus.practice_candidate_count ?? 0) >= 2 ? (
                <Link
                    to={`/puzzles?mode=focus_practice&focus_cause=${encodeURIComponent(focus.cause)}`}
                    className="km-interactive km-focus-visible inline-flex items-center mt-4 min-h-11 border border-primary/40 rounded-sm px-4 py-2 font-sans text-sm text-primary hover:bg-primary/10 transition-colors"
                >
                    Practice this focus · {focus.practice_candidate_count} positions
                </Link>
            ) : (
                // Nothing of this pattern is due, and training early would
                // re-anchor intervals. A focus practice button is truthful only
                // when the server has confirmed a safe bounded set.
                <p className="font-sans text-xs text-primary/70 mt-4">
                    Nothing from this pattern is due right now — it will come
                    back around.
                </p>
            )}

            {focus.runner_up && (
                // Naming the runner-up keeps a close call from reading as a
                // landslide, and answers "what about the other thing?".
                <p className="font-sans text-xs text-primary/70 mt-3">
                    After that: {focus.runner_up}.
                </p>
            )}
        </Shell>
    );
}

function Shell({ children }: { children: React.ReactNode }) {
    return (
        <section
            aria-labelledby="todays-focus-heading"
            className="bg-primary/5 border border-primary/10 rounded-sm p-6 backdrop-blur-sm"
        >
            <h2
                id="todays-focus-heading"
                className="font-sans font-normal text-xs uppercase tracking-widest text-primary/70 mb-3"
            >
                Today’s focus
            </h2>
            {children}
        </section>
    );
}
