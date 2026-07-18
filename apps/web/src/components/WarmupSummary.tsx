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
      className="bg-primary/5 border border-blue-500/20 rounded-sm p-8 animate-teedin"
      role="region"
      aria-labelledby="warmup-summary-heading"
    >
      <div className="text-center mb-6">
        <h2 id="warmup-summary-heading" className="text-3xl font-serif text-primary mb-2">
          Warmup Complete! 🎯
        </h2>
        <p className="text-primary/60 font-sans">
          Here's how you did after your break
        </p>
      </div>

      {/* Accuracy Score */}
      <div className="text-center mb-8" role="status" aria-live="polite">
        <div className="text-6xl font-serif text-primary mb-2" aria-label={`Overall accuracy: ${accuracy} percent`}>
          {accuracy}%
        </div>
        <p className="text-primary/60 font-sans">Overall Accuracy</p>
      </div>

      {/* Pattern Retention Grid */}
      <div className="grid grid-cols-2 gap-6 mb-8">
        <div className="text-center">
          <div className="text-3xl font-serif text-green-600" aria-label={`${sessionSummary.pass_count} patterns retained`}>
            {sessionSummary.pass_count}
          </div>
          <div className="text-xs uppercase tracking-widest text-primary/40 mt-1">
            Patterns Retained
          </div>
        </div>
        <div className="text-center">
          <div className="text-3xl font-serif text-red-500" aria-label={`${sessionSummary.fail_count} patterns need review`}>
            {sessionSummary.fail_count}
          </div>
          <div className="text-xs uppercase tracking-widest text-primary/40 mt-1">
            Need Review
          </div>
        </div>
      </div>

      {/* Feedback Message */}
      <div className="bg-primary/5 border border-primary/10 rounded-sm p-6 mb-6">
        <p className="text-primary/60 font-sans text-center">
          {getFeedbackMessage(accuracy)}
        </p>
      </div>

      {/* Continue Button */}
      <button
        type="button"
        onClick={onContinue}
        className="w-full px-6 py-3 bg-primary text-bg-primary rounded-sm font-serif transition-opacity hover:opacity-90 cursor-pointer km-focus-visible"
        aria-label="Continue to dashboard"
      >
        Continue to Dashboard
      </button>
    </section>
  );
}
