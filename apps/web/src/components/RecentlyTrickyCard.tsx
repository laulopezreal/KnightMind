import { formatRelativeTime } from '../utils/time';
import { type TrickyPuzzle } from '../api/users';

interface RecentlyTrickyCardProps {
  puzzles: TrickyPuzzle[];
}

export function RecentlyTrickyCard({ puzzles }: RecentlyTrickyCardProps) {
  if (puzzles.length === 0) {
    return null; // Don't render if no tricky puzzles
  }

  return (
    <section
      className="bg-primary/5 border border-primary/5 rounded-sm p-6"
      aria-labelledby="tricky-title"
    >
      <h3 id="tricky-title" className="text-xl md:text-2xl font-serif text-primary mb-4">
        Recently tricky
      </h3>

      <div className="space-y-0">
        {puzzles.map((puzzle, index) => (
          <div
            key={puzzle.puzzle_id}
            className={`py-3 ${
              index !== puzzles.length - 1 ? 'border-b border-primary/5' : ''
            }`}
          >
            {/* Puzzle name */}
            <p className="font-serif text-primary mb-1">
              {puzzle.title}
            </p>

            {/* Metadata */}
            <p className="text-xs text-primary/40 font-sans">
              Failed {puzzle.fail_count}× · Last tried {formatRelativeTime(puzzle.last_attempted_at)}
            </p>
          </div>
        ))}
      </div>

      {/* Encouraging footer */}
      <p className="mt-4 pt-4 border-t border-primary/5 text-xs text-primary/50 font-sans italic">
        These puzzles are helping you learn. Keep practicing!
      </p>
    </section>
  );
}
