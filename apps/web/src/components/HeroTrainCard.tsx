import { formatRelativeTime } from '../utils/time';

interface HeroTrainCardProps {
  dueCount: number;
  nextReviewAt: string | null;
  needsWarmup: boolean;
  daysSinceLastSession: number;
  onStartSession: () => void;
}

export function HeroTrainCard({
  dueCount,
  nextReviewAt,
  needsWarmup,
  daysSinceLastSession,
  onStartSession
}: HeroTrainCardProps) {
  // Determine the state and messaging
  const isZeroDue = dueCount === 0;
  const title = needsWarmup ? '🧠 Welcome Back' : '🧠 Train Today';

  const supportingText = needsWarmup
    ? `You've been away ${daysSinceLastSession} days. Let's do a quick warmup to see what stuck!`
    : isZeroDue
    ? "You're all caught up!"
    : 'Most people improve by solving these today.';

  const buttonText = needsWarmup
    ? 'Start Warmup (5 puzzles)'
    : isZeroDue
    ? 'Browse Puzzles'
    : 'Start Session →';

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

      {/* Due Count (or celebration) */}
      {isZeroDue ? (
        <div className="mb-6">
          <p className="text-4xl md:text-5xl font-mono text-primary mb-2">
            🎉
          </p>
          <p className="text-lg text-primary/60 font-sans">
            {supportingText}
          </p>
        </div>
      ) : (
        <div className="mb-6">
          <p className="text-6xl md:text-7xl font-mono text-primary mb-2">
            {dueCount}
          </p>
          <p className="text-primary/60 text-sm font-sans">
            puzzle{dueCount !== 1 ? 's' : ''} due
          </p>
        </div>
      )}

      {/* Supporting Text */}
      {!isZeroDue && (
        <p className="text-lg text-primary/60 font-sans mb-8">
          {supportingText}
        </p>
      )}

      {/* Primary CTA */}
      <button
        type="button"
        onClick={onStartSession}
        disabled={isZeroDue && !needsWarmup}
        className={`px-8 py-3 bg-accent text-bg-primary rounded-sm font-serif text-lg transition-colors km-focus-visible ${
          isZeroDue && !needsWarmup ? 'km-interactive-disabled opacity-50' : 'km-interactive'
        }`}
        aria-label={buttonText}
      >
        {buttonText}
      </button>

      {/* Next Review Info */}
      {nextReviewAt && (
        <p className="mt-6 text-sm text-primary/40 font-sans">
          Next review {isZeroDue ? 'in' : 'window'}: {formatRelativeTime(nextReviewAt)}
        </p>
      )}
    </section>
  );
}
