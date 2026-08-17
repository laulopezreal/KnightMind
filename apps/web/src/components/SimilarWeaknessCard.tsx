import { Link } from 'react-router-dom';
import { type SimilarPuzzlesResponse } from '../api/puzzles';
import { formatMotifName } from '../utils/motif';

interface SimilarWeaknessCardProps {
    data: SimilarPuzzlesResponse | null;
    /** Excluded from its own list, so the current puzzle never links to itself. */
    currentPuzzleId: string;
}

/**
 * "More like this weakness" — the other puzzles this user got wrong for the
 * same reason.
 *
 * Renders nothing when there are no siblings. An undiagnosed puzzle, an
 * unclassified cause, or a weakness with exactly one example are all ordinary
 * outcomes, and an empty card headed "More like this" would read as a fault in
 * the app rather than the honest answer that there is nothing to show.
 */
export function SimilarWeaknessCard({ data, currentPuzzleId }: SimilarWeaknessCardProps) {
    const puzzles = (data?.puzzles ?? []).filter((p) => p.id !== currentPuzzleId);
    if (puzzles.length === 0) {
        return null;
    }

    return (
        <section
            className="bg-primary/5 border border-primary/10 rounded-sm p-6"
            aria-labelledby="similar-weakness-title"
        >
            <h3
                id="similar-weakness-title"
                className="text-xl md:text-2xl font-serif text-primary mb-1"
            >
                More like this weakness
            </h3>

            {/* The reason states what these puzzles share. It comes from the
                server so the wording tracks how closely they actually matched
                — a cause-only match must not read like an exact one. */}
            {data?.reason && (
                <p className="text-sm text-primary/70 font-sans mb-4">{data.reason}</p>
            )}

            <ul className="space-y-0">
                {puzzles.map((puzzle, index) => (
                    <li key={puzzle.id}>
                        <Link
                            to={`/library/${encodeURIComponent(puzzle.id)}`}
                            className={`block py-3 km-interactive rounded-sm px-2 -mx-2 ${
                                index !== puzzles.length - 1
                                    ? 'border-b border-primary/5'
                                    : ''
                            }`}
                        >
                            <p className="font-serif text-primary mb-1">
                                {puzzle.display_name}
                            </p>
                            <p className="text-xs text-primary/70 font-sans">
                                {puzzle.primary_motif && <>{formatMotifName(puzzle.primary_motif)} · </>}
                                {puzzle.difficulty}
                                {puzzle.fail_count > 0 && (
                                    <> · Failed {puzzle.fail_count}&times;</>
                                )}
                            </p>
                        </Link>
                    </li>
                ))}
            </ul>
        </section>
    );
}
