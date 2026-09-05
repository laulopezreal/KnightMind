import { Link } from 'react-router-dom';
import type { SessionSummary } from '../api/sessions';
import { calculateAccuracy } from '../utils/accuracy';

interface WarmupSummaryProps {
  sessionSummary: SessionSummary;
  onContinue: () => void;
}

/**
 * WarmupSummary displays results after completing a warmup diagnostic session
 * Shows accuracy, pass/fail counts, and personalized feedback
 */
export function WarmupSummary({ sessionSummary, onContinue }: WarmupSummaryProps) {
  const accuracy = calculateAccuracy(sessionSummary.pass_count, sessionSummary.fail_count);
  const missedPuzzles = sessionSummary.missed_puzzles;
  const hasMissedPuzzles = Boolean(missedPuzzles?.length);

  // Determine feedback based on performance
  const getFeedbackMessage = (acc: number): string => {
    if (acc >= 80) {
      return "Great retention! You're ready to continue where you left off.";
    } else if (acc >= 60) {
      return "Some patterns need brushing up. Let's get back into rhythm!";
    } else {
      return "Time to rebuild those neural pathways. Let's start with the basics.";
    }
  };

  return (
    <section
      className="bg-primary/5 border border-status-new-soft rounded-sm p-8 animate-teedin"
      role="region"
      aria-labelledby="warmup-summary-heading"
    >
      <div className="text-center mb-6">
        <h2 id="warmup-summary-heading" className="text-3xl font-serif text-primary mb-2">
          Warmup complete
        </h2>
        <p className="text-primary/70 font-sans">
          Here's how you did after your break
        </p>
      </div>

      {/* Accuracy Score */}
      <div className="text-center mb-8" role="status" aria-live="polite">
        <div className="text-6xl font-serif text-primary mb-2" aria-label={`Overall accuracy: ${accuracy} percent`}>
          {accuracy}%
        </div>
        <p className="text-primary/70 font-sans">Overall Accuracy</p>
      </div>

      {/* Pattern Retention Grid */}
      <div className="grid grid-cols-2 gap-6 mb-8">
        <div className="text-center">
          <div className="text-3xl font-serif text-positive" aria-label={`${sessionSummary.pass_count} patterns retained`}>
            {sessionSummary.pass_count}
          </div>
          <div className="text-xs uppercase tracking-widest text-primary/70 mt-1">
            Patterns Retained
          </div>
        </div>
        <div className="text-center">
          <div className="text-3xl font-serif text-negative" aria-label={`${sessionSummary.fail_count} patterns need review`}>
            {sessionSummary.fail_count}
          </div>
          <div className="text-xs uppercase tracking-widest text-primary/70 mt-1">
            Need Review
          </div>
        </div>
      </div>

      {/* Feedback Message */}
      <div className="bg-primary/5 border border-primary/10 rounded-sm p-6 mb-6">
        <p className="text-primary/70 font-sans text-center">
          {getFeedbackMessage(accuracy)}
        </p>
      </div>

      {hasMissedPuzzles && missedPuzzles && (
        <section className="mb-6 border-y border-primary/10 py-5" aria-labelledby="warmup-missed-puzzles-heading">
          <h3 id="warmup-missed-puzzles-heading" className="text-lg font-serif text-primary mb-1">
            {missedPuzzles.length === 1 ? 'Missed puzzle' : `Missed puzzles (${missedPuzzles.length})`}
          </h3>
          <p className="text-sm text-primary/70 mb-3">Review what to learn from this warmup.</p>
          <ul className="divide-y divide-primary/10" aria-label="Missed puzzles">
            {missedPuzzles.map((missedPuzzle) => (
              <li
                key={missedPuzzle.puzzle_id}
                className="flex flex-col items-stretch gap-1 py-3 sm:flex-row sm:items-center sm:gap-3"
              >
                <div className="min-w-0 flex-1">
                  <span className="block text-sm font-serif text-primary whitespace-normal break-words">
                    {missedPuzzle.display_name}
                  </span>
                  {missedPuzzle.cause_label && (
                    <span className="block text-xs text-primary/70 mt-1 whitespace-normal break-words">
                      {missedPuzzle.cause_label}
                    </span>
                  )}
                </div>
                <Link
                  to={`/library/${missedPuzzle.puzzle_id}?from=session`}
                  className="self-start sm:self-auto shrink-0 inline-flex items-center justify-center min-h-11 min-w-11 text-xs font-serif text-primary/70 underline underline-offset-2 hover:text-primary transition-colors km-focus-visible"
                  aria-label={`Review ${missedPuzzle.display_name}`}
                >
                  Review
                </Link>
              </li>
            ))}
          </ul>
        </section>
      )}

      <button
        type="button"
        onClick={onContinue}
        className="w-full px-6 py-3 bg-primary text-bg-primary rounded-sm font-serif transition-opacity hover:opacity-90 cursor-pointer km-focus-visible"
      >
        Back to Dashboard
      </button>
    </section>
  );
}
