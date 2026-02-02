import { formatRelativeTime } from '../utils/time';

interface HeroTrainCardProps {
  dueCount: number;
  nextReviewAt: string | null;
  needsWarmup: boolean;
  daysSinceLastSession: number;
  totalSessions: number;
  onStartSession: () => void;
}

export function HeroTrainCard({
  dueCount,
  nextReviewAt,
  needsWarmup,
  daysSinceLastSession,
  totalSessions,
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

  const supportingText = isFirstTime
    ? `You have ${dueCount} puzzle${dueCount !== 1 ? 's' : ''} waiting. Complete your first session to see your tactical profile!`
    : needsWarmup
    ? `You've been away ${daysSinceLastSession} days. Let's do a quick warmup to see what stuck!`
    : isZeroDue
    ? 'Great work! Check back later for more puzzles.'
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
      {/* Title */}
      <h2
        id="hero-title"
        className="text-3xl md:text-4xl font-serif text-primary mb-6"
      >
        {title}
      </h2>

      {/* Due Count */}
      <div className="mb-6">
        <p className="text-6xl md:text-7xl font-mono text-primary mb-2">
          {dueCount}
        </p>
        <p className="text-primary/60 text-sm font-sans">
          puzzle{dueCount !== 1 ? 's' : ''} due
        </p>
      </div>

      {/* Supporting Text */}
      <p className="text-lg text-primary/60 font-sans mb-8">
        {supportingText}
      </p>

      {/* Primary CTA */}
      <button
        type="button"
        onClick={onStartSession}
        className="px-8 py-3 bg-accent text-bg-primary rounded-sm font-serif text-lg transition-colors km-focus-visible km-interactive"
      >
        {buttonText}
      </button>

      {/* Next Review Info */}
      {nextReviewAt && (
        <p className="mt-6 text-sm text-primary/40 font-sans">
          Next review: {formatRelativeTime(nextReviewAt)}
        </p>
      )}
    </section>
  );
}
