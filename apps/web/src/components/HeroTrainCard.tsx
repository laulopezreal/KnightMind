import { formatRelativeTime } from '../utils/time';
import { needsImportFirst } from '../utils/trainEntry';

interface HeroTrainCardProps {
  dueCount: number;
  nextReviewAt: string | null;
  needsWarmup: boolean;
  daysSinceLastSession: number;
  totalSessions: number;
  /** Puzzles that become due within the next 4 hours (already excludes due_now). */
  dueIn4h?: number;
  /** Server-derived: user has at least one completed session on today's UTC day. */
  completedToday?: boolean;
  onStartSession: () => void;
}

export function HeroTrainCard({
  dueCount,
  nextReviewAt,
  needsWarmup,
  daysSinceLastSession,
  totalSessions,
  dueIn4h = 0,
  completedToday = false,
  onStartSession
}: HeroTrainCardProps) {
  // Determine the state and messaging
  const isFirstTime = totalSessions === 0;
  const isZeroDue = dueCount === 0;

  // completed-today states take precedence over Train Today / caught-up, but
  // not over first-time or warmup (which need different actions).
  const isCompletedToday = completedToday && !isFirstTime && !needsWarmup;

  const title = isFirstTime
    ? 'Ready to Start Training?'
    : needsWarmup
    ? 'Welcome Back'
    : isCompletedToday
    ? "Today's training is complete"
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
      : `Your first session is 5 puzzles. ${dueCount} puzzle${dueCount !== 1 ? 's are' : ' is'} available for review.`
    : needsWarmup
    ? `You've been away ${daysSinceLastSession} days. Let's do a quick warmup to see what stuck!`
    : isCompletedToday && !isZeroDue
    ? `You completed a training session today. More puzzles are ready whenever you want to keep going.`
    : isCompletedToday && isZeroDue
    // Prepend the honest completion acknowledgement, then the regular caught-up guidance.
    ? `Today's training is complete. ${caughtUpText}`
    : isZeroDue
    ? caughtUpText
    : 'A Standard session is 5 puzzles. The rest stay available whenever you want them.';

  // A first-timer with nothing due has no session to start — the supporting
  // copy already tells them to import, so the CTA must agree. The same
  // predicate drives the route (see utils/trainEntry).
  const needsImport = needsImportFirst({ totalSessions, dueCount });

  const buttonText = needsImport
    ? 'Import Your Games'
    : isFirstTime
    ? 'Start First Session'
    : needsWarmup
    ? 'Start Warmup (5 puzzles)'
    : isCompletedToday && !isZeroDue
    ? 'Train more (5 puzzles)'
    : isZeroDue
    ? 'Browse Puzzles'
    : 'Start Standard Session (5 puzzles)';

  // Only the caught-up branch (non-completed-today) prints the next-review time
  // in the body. Scope the caption-suppression guard to exactly that branch.
  const nextReviewShownInBody =
    !isFirstTime && !needsWarmup && isZeroDue && dueIn4h === 0 && !!nextReviewAt;

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
          {/* A big "0 puzzles due" next to "import your games to generate your
              first set" is noise — there is no queue to report yet. */}
          {!needsImport && (
            <div className="mb-4">
              <p className="text-4xl md:text-5xl font-mono text-primary leading-none">
                {dueCount}
              </p>
              <p className="text-primary/70 text-sm font-sans mt-1">
                puzzle{dueCount !== 1 ? 's' : ''} available for review
              </p>
            </div>
          )}

          <button
            type="button"
            onClick={onStartSession}
            className="min-h-11 px-8 py-3 bg-primary text-bg-primary rounded-sm font-serif text-lg transition-opacity hover:opacity-90 cursor-pointer km-focus-visible"
          >
            {buttonText}
          </button>

          {nextReviewAt && !nextReviewShownInBody && (
            <p className="mt-3 text-sm text-primary/70 font-sans">
              Next review: {formatRelativeTime(nextReviewAt)}
            </p>
          )}

        </div>
      </div>
    </section>
  );
}
