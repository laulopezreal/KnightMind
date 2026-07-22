import { Link } from 'react-router-dom';
import { formatRelativeTime } from '../utils/time';
import { type TrickyPuzzle } from '../api/users';

interface RecentlyTrickyCardProps {
  puzzles: TrickyPuzzle[];
  totalCount: number;
}

export function RecentlyTrickyCard({ puzzles, totalCount }: RecentlyTrickyCardProps) {
  if (puzzles.length === 0) {
    return null; // Don't render if no tricky puzzles
  }

  const remaining = totalCount - puzzles.length;

  return (
    <section
      className="bg-primary/5 border border-primary/10 rounded-sm p-6"
      aria-labelledby="tricky-title"
    >
      <h3 id="tricky-title" className="text-xl md:text-2xl font-serif text-primary mb-4">
        Recently tricky
      </h3>

      <div className="space-y-0">
        {puzzles.map((puzzle, index) => (
          <Link
            key={puzzle.puzzle_id}
            to={`/library/${encodeURIComponent(puzzle.puzzle_id)}`}
            className={`block py-3 km-interactive rounded-sm px-2 -mx-2 ${
              index !== puzzles.length - 1 ? 'border-b border-primary/5' : ''
            }`}
          >
            {/* Puzzle name */}
            <p className="font-serif text-primary mb-1">
              {puzzle.title}
            </p>

            {/* Metadata */}
            <p className="text-xs text-primary/70 font-sans">
              Failed {puzzle.fail_count}&times; · Last tried {formatRelativeTime(puzzle.last_attempted_at)}
            </p>
          </Link>
        ))}
      </div>

      {remaining > 0 && (
        <p className="mt-4 pt-4 border-t border-primary/5 text-xs text-primary/70 font-sans">
          and {remaining} more tricky puzzle{remaining !== 1 ? 's' : ''}
        </p>
      )}
    </section>
  );
}
