import { Link } from 'react-router-dom';
import type { MistakeCause, MistakeCausesResponse } from '../api/users';

interface TopMistakeCausesCardProps {
    data: MistakeCausesResponse;
}

/**
 * Why this player's mistakes happen, across their whole corpus.
 *
 * The card's job is to be honest about how much it knows. Two rules shape it:
 *
 * 1. **A count is not a tendency.** A cause seen once or twice is shown with its
 *    real count but explicitly not ranked or recommended — below the threshold,
 *    one bad afternoon looks identical to a habit. Only causes above it get a
 *    "practise this" call to action.
 * 2. **Accuracy is verified-only or absent.** The API returns `accuracy: null`
 *    until there are enough server-verified attempts, and a missing rate renders
 *    as "not enough attempts yet" rather than as 0%.
 *
 * "Cause unclear" is reported rather than hidden — omitting it would overstate
 * how much of the corpus is actually understood — but never as something to
 * train.
 */
export function TopMistakeCausesCard({ data }: TopMistakeCausesCardProps) {
    const { causes, total_diagnosed, pending, min_for_ranking } = data;

    if (causes.length === 0) {
        return (
            <Shell pending={pending}>
                <p className="font-sans text-sm text-primary/60">
                    {pending > 0
                        ? 'Your mistakes haven’t been analysed yet.'
                        : 'No diagnosed mistakes yet. Import some games to get started.'}
                </p>
            </Shell>
        );
    }

    const ranked = causes.filter((c) => !c.insufficient_data && !c.is_unclassified);
    const thin = causes.filter((c) => c.insufficient_data && !c.is_unclassified);
    const unclear = causes.find((c) => c.is_unclassified);

    return (
        <Shell pending={pending}>
            <div className="space-y-6">
                {ranked.length > 0 ? (
                    <ol className="space-y-4">
                        {ranked.map((cause) => (
                            <CauseRow key={cause.cause} cause={cause} total={total_diagnosed} />
                        ))}
                    </ol>
                ) : (
                    <p className="font-sans text-sm text-primary/60">
                        No cause has come up {min_for_ranking} times yet, so nothing here is
                        a pattern worth training. Keep playing — this fills in on its own.
                    </p>
                )}

                {thin.length > 0 && (
                    <div>
                        <h3 className="font-sans text-xs uppercase tracking-widest text-primary/60 mb-2">
                            Seen too few times to call a pattern
                        </h3>
                        <div className="flex flex-wrap gap-2">
                            {thin.map((cause) => (
                                <span
                                    key={cause.cause}
                                    className="font-sans text-xs text-primary/70 border border-primary/20 rounded-sm px-2 py-1"
                                >
                                    {cause.label} ({cause.mistakes})
                                </span>
                            ))}
                        </div>
                    </div>
                )}

                {unclear && (
                    <p className="font-sans text-xs text-primary/50">
                        {unclear.mistakes} mistake{unclear.mistakes === 1 ? '' : 's'} with no
                        clear cause.
                    </p>
                )}
            </div>
        </Shell>
    );
}

function CauseRow({ cause, total }: { cause: MistakeCause; total: number }) {
    const share = total > 0 ? Math.round((cause.mistakes / total) * 100) : 0;

    return (
        <li className="border-l-2 border-primary/30 pl-4">
            <div className="flex items-baseline justify-between gap-4 flex-wrap">
                <p className="font-serif text-xl text-primary">{cause.label}</p>
                <span className="font-mono text-sm text-primary/70">
                    {cause.mistakes} mistake{cause.mistakes === 1 ? '' : 's'} · {share}%
                </span>
            </div>

            <div className="flex items-center gap-3 mt-1 flex-wrap">
                {cause.dominant_phase && (
                    <span className="font-sans text-xs text-primary/60 capitalize">
                        mostly {cause.dominant_phase}
                    </span>
                )}
                {cause.dominant_opening && (
                    // Only rendered when one opening holds a real majority of
                    // this cause's mistakes — see _dominant. "Common in X" off a
                    // plurality would be a claim the data doesn't carry.
                    <span className="font-sans text-xs text-primary/60">
                        common in {cause.dominant_opening}
                    </span>
                )}
                <span className="font-sans text-xs text-primary/60">
                    {accuracyLabel(cause)}
                </span>
            </div>

            <Link
                to={`/library?cause=${encodeURIComponent(cause.cause)}`}
                className="km-interactive km-focus-visible km-inline-link inline-block mt-2 text-primary text-xs font-medium underline decoration-primary/30 underline-offset-4 transition-colors"
            >
                Practise this
            </Link>
        </li>
    );
}

/**
 * Why there is no rate yet, specifically.
 *
 * The API withholds accuracy for two different reasons and they need different
 * words: too few attempts, or enough attempts but all on one puzzle. Solving
 * the same position repeatedly shows recall of that position, not that the
 * habit has changed — telling the user "not enough attempts" when they have
 * plenty would read as a bug.
 */
function accuracyLabel(cause: MistakeCause): string {
    if (cause.accuracy !== null && cause.accuracy !== undefined) {
        return `${Math.round(cause.accuracy * 100)}% solved when retried`;
    }
    if (cause.verified_attempts > 0 && cause.verified_puzzles <= 1) {
        return 'only one puzzle tried so far';
    }
    return 'not enough attempts yet';
}

function Shell({ children, pending }: { children: React.ReactNode; pending: number }) {
    return (
        <section
            aria-labelledby="mistake-causes-heading"
            className="bg-primary/5 border border-primary/10 rounded-sm p-6 backdrop-blur-sm"
        >
            <div className="flex items-baseline justify-between gap-4 mb-4 flex-wrap">
                <h2
                    id="mistake-causes-heading"
                    className="font-serif text-2xl text-primary"
                >
                    Why your mistakes happen
                </h2>
                {pending > 0 && (
                    // Surfaced so a short list never reads as "you make no
                    // mistakes" when it actually means "not analysed yet".
                    <span className="font-sans text-xs text-primary/60">
                        {pending} still to analyse
                    </span>
                )}
            </div>
            {children}
        </section>
    );
}
