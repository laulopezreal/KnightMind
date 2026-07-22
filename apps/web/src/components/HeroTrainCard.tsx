import { formatRelativeTime } from '../utils/time';

interface HeroTrainCardProps {
  dueCount: number;
  nextReviewAt: string | null;
  needsWarmup: boolean;
  daysSinceLastSession: number;
  totalSessions: number;
  /** Puzzles that become due within the next 4 hours (already excludes due_now). */
  dueIn4h?: number;
  onStartSession: () => void;
}

export function HeroTrainCard({
  dueCount,
  nextReviewAt,
  needsWarmup,
  daysSinceLastSession,
  totalSessions,
  dueIn4h = 0,
  onStartSession
}: HeroTrainCardProps) {
  // Determine the state and messaging
  const isFirstTime = totalSessions === 0;
  const isZeroDue = dueCount === 0;

  const title = isFirstTime
    ? 'Ready to Start Training?'
    : needsWarmup
    ? 'Welcome Back'
    : isZeroDue
    ? 'All Caught Up'
    : 'Train Today';

  // "Caught up" copy uses data we already fetch: prefer a concrete
  // "N ready within 4 hours", then a next-review time, then a generic nudge —
  // so the one screen where the user has nothing to do still tells them when
  // to come back instead of a dead-end "check back later".
  const caughtUpText = dueIn4h > 0
    ? `Great work! ${dueIn4h} more puzzle${dueIn4h !== 1 ? 's' : ''} will be ready within 4 hours.`
    : nextReviewAt
    ? `Great work! Your next review is ${formatRelativeTime(nextReviewAt)}.`
    : 'Great work! Check back later for more puzzles.';

  const supportingText = isFirstTime
    ? isZeroDue
      // First-timer with nothing generated yet: don't claim "0 puzzles
      // waiting" (self-contradicting) — point them at the real next step.
      ? 'Import your Chess.com games to generate your first set of puzzles.'
      : `You have ${dueCount} puzzle${dueCount !== 1 ? 's' : ''} waiting. Complete your first session to see your tactical profile!`
    : needsWarmup
    ? `You've been away ${daysSinceLastSession} days. Let's do a quick warmup to see what stuck!`
    : isZeroDue
    ? caughtUpText
    : 'Most people improve by solving these today.';

  const buttonText = isFirstTime
    ? 'Start First Session'
    : needsWarmup
    ? 'Start Warmup (5 puzzles)'
    : isZeroDue
    ? 'Browse Puzzles'
    : 'Start Session';

  return (
    <section
      className="bg-primary/10 rounded-sm p-8 md:p-12 shadow-lg shadow-primary/5"
      aria-labelledby="hero-title"
    >
      <div className="flex flex-col md:flex-row md:items-center md:justify-between gap-6">
        {/* Left: Title + Supporting Text */}
        <div className="flex-1 min-w-0">
          <h2
            id="hero-title"
            className="text-3xl md:text-4xl font-serif text-primary mb-3"
          >
            {title}
          </h2>
          <p className="text-lg text-primary/70 font-sans">
            {supportingText}
          </p>
        </div>

        {/* Right: Due Count + CTA + Next Review */}
        <div className="flex flex-col items-center text-center shrink-0 md:min-w-44">
          <div className="mb-4">
            <p className="text-4xl md:text-5xl font-mono text-primary leading-none">
              {dueCount}
            </p>
            <p className="text-primary/70 text-sm font-sans mt-1">
              puzzle{dueCount !== 1 ? 's' : ''} due
            </p>
          </div>

          <button
            type="button"
            onClick={onStartSession}
            className="px-8 py-3 bg-primary text-bg-primary rounded-sm font-serif text-lg transition-opacity hover:opacity-90 cursor-pointer km-focus-visible"
          >
            {buttonText}
          </button>

          {nextReviewAt && (
            <p className="mt-3 text-sm text-primary/70 font-sans">
              Next review: {formatRelativeTime(nextReviewAt)}
            </p>
          )}
        </div>
      </div>
    </section>
  );
}
