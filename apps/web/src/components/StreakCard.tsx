import { formatRelativeTime } from '../utils/time';

interface StreakCardProps {
  streakDays: number;
  lastSessionAt: string | null;
}

export function StreakCard({ streakDays, lastSessionAt }: StreakCardProps) {
  const encouragement = streakDays > 0
    ? 'Keep going'
    : lastSessionAt
    ? 'Resume your streak'
    : 'Start your streak today';

  return (
    <section
      className="bg-primary/5 border border-primary/5 rounded-sm p-6"
      aria-labelledby="streak-title"
    >
      <h3 id="streak-title" className="text-xl md:text-2xl font-serif text-primary mb-4">
        Consistency
      </h3>

      <div className="space-y-4">
        {/* Streak count */}
        <div>
          <p className="text-4xl font-mono text-primary leading-tight">
            {streakDays}
          </p>
          <p className="text-xs text-primary/70 font-sans">
            day streak
          </p>
        </div>

        {/* Encouraging message */}
        <p className="text-sm text-primary/70 font-sans">
          {encouragement}
        </p>

        {/* Last session timestamp */}
        {lastSessionAt && (
          <p className="text-xs text-primary/70 font-sans pt-2 border-t border-primary/5">
            Last session: {formatRelativeTime(lastSessionAt)}
          </p>
        )}
      </div>
    </section>
  );
}
