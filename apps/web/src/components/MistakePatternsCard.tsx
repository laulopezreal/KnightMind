import { Link } from 'react-router-dom';
import type { MistakePattern, MistakePatternsResponse } from '../api/users';

interface MistakePatternsCardProps {
    data: MistakePatternsResponse;
}

/**
 * The player's mistake habits, named and explained.
 *
 * This is the coaching layer: not "loose_piece_awareness — 8 mistakes" but
 * "Loose Piece Syndrome — you calculate your own threat first and skip the scan
 * for what of yours is hanging."
 *
 * A cause only appears here once it has actually recurred. Below that, it stays
 * a count on the causes card and is summarised as "N more not seen often enough
 * yet" — naming a habit off two occurrences is the overreach this whole feature
 * exists to avoid.
 */
export function MistakePatternsCard({ data }: MistakePatternsCardProps) {
    const { patterns, below_threshold, pending } = data;

    if (patterns.length === 0 && below_threshold === 0 && pending === 0) {
        return null;
    }

    return (
        <section
            aria-labelledby="mistake-patterns-heading"
            className="bg-primary/5 border border-primary/10 rounded-sm p-6 backdrop-blur-sm"
        >
            <h2
                id="mistake-patterns-heading"
                className="font-serif text-2xl text-primary mb-4"
            >
                Your patterns
            </h2>

            {patterns.length > 0 ? (
                <ol className="space-y-6">
                    {patterns.map((pattern) => (
                        <PatternRow key={pattern.cause} pattern={pattern} />
                    ))}
                </ol>
            ) : (
                <p className="font-sans text-sm text-primary/60">
                    {pending > 0
                        ? 'Still analysing your mistakes — patterns appear once one recurs.'
                        : 'No habit has recurred often enough to name yet.'}
                </p>
            )}

            {below_threshold > 0 && (
                <p className="font-sans text-xs text-primary/50 mt-4">
                    {below_threshold} other cause{below_threshold === 1 ? '' : 's'} seen
                    too few times to call a pattern.
                </p>
            )}
        </section>
    );
}

function PatternRow({ pattern }: { pattern: MistakePattern }) {
    return (
        <li className="border-l-2 border-primary/30 pl-4">
            <p className="font-serif text-xl text-primary">{pattern.name}</p>
            <p className="font-sans text-sm text-primary/80 mt-1 leading-relaxed">
                {pattern.description}
            </p>
            <div className="flex items-center gap-3 mt-2 flex-wrap font-sans text-xs text-primary/60">
                <span>
                    {pattern.mistakes} time{pattern.mistakes === 1 ? '' : 's'}
                </span>
                {pattern.recent_mistakes > 0 && (
                    // Recency is what makes a habit current rather than historic,
                    // so it earns its own line rather than being folded into a score.
                    <span>{pattern.recent_mistakes} in recent games</span>
                )}
                {pattern.dominant_phase && (
                    <span className="capitalize">mostly {pattern.dominant_phase}</span>
                )}
            </div>
            <Link
                to={`/library?cause=${encodeURIComponent(pattern.cause)}`}
                className="km-interactive km-focus-visible km-inline-link inline-block mt-2 text-primary text-xs font-medium underline decoration-primary/30 underline-offset-4 transition-colors"
            >
                Train this pattern
            </Link>
        </li>
    );
}
