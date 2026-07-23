import { formatRelativeTime } from '../utils/time';

interface StreakCardProps {
  streakDays: number;
  lastSessionAt: string | null;
}

// The rhythm strip shows up to a week of momentum at a glance. It is derived
// from the streak length alone (the card has no per-day history), so it fills
// the most-recent `min(streakDays, 7)` cells — an honest "current run, up to a
// week" view rather than a literal calendar.
const STRIP_DAYS = 7;

export function StreakCard({ streakDays, lastSessionAt }: StreakCardProps) {
  const encouragement = streakDays > 0
    ? 'Keep going'
    : lastSessionAt
    ? 'Resume your streak'
    : 'Start your streak today';

  const filled = Math.min(streakDays, STRIP_DAYS);

  return (
    <section
      className="bg-primary/5 border border-primary/10 rounded-sm p-6"
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

        {/* Rhythm strip — one labelled image so screen readers hear a single
            summary instead of seven separate cells. Most-recent day is on the
            right, matching the left-to-right timeline everywhere else. */}
        <div
          className="flex gap-1"
          role="img"
          aria-label={
            streakDays === 0
              ? 'No active streak'
              : `Current streak: ${streakDays} day${streakDays !== 1 ? 's' : ''}${streakDays > STRIP_DAYS ? ` (showing the last ${STRIP_DAYS})` : ''}`
          }
        >
          {Array.from({ length: STRIP_DAYS }, (_, i) => {
            // Fill from the right: the rightmost `filled` cells are active.
            const isActive = i >= STRIP_DAYS - filled;
            return (
              <div
                key={i}
                aria-hidden="true"
                className={`h-2 flex-1 rounded-full transition-colors ${
                  isActive ? 'bg-primary/70' : 'bg-primary/10'
                }`}
              />
            );
          })}
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
