import type { RecentFormData } from '../api/users';

interface MomentumCardProps {
  recentForm: RecentFormData;
}

export function MomentumCard({ recentForm }: MomentumCardProps) {
  const { last_20_results, accuracy, trend } = recentForm;

  // Map trend to informational text (not judgmental)
  const trendText = trend === 'up'
    ? 'Improving'
    : trend === 'down'
    ? 'Slight dip'
    : 'Steady';

  return (
    <section
      className="bg-primary/5 border border-primary/5 rounded-sm p-6"
      aria-labelledby="momentum-title"
    >
      <h3 id="momentum-title" className="text-xl md:text-2xl font-serif text-primary mb-4">
        Momentum
      </h3>

      <div className="space-y-4">
        {/* Visual bar of last 20 puzzles. The row is a single labelled image so
            screen readers announce one summary instead of 20 separate squares
            (aria-label on a role-less div is also invalid ARIA). */}
        <div>
          <p className="text-xs text-primary/60 font-sans mb-2">
            {last_20_results.length > 0 ? `Last ${last_20_results.length} puzzles` : 'Recent puzzles'}
          </p>
          <div
            className="flex gap-1 flex-wrap"
            role="img"
            aria-label={
              last_20_results.length === 0
                ? 'No recent attempts yet'
                : `Last ${last_20_results.length} puzzles: ${last_20_results.filter((r) => r === 'pass').length} correct, ${last_20_results.filter((r) => r === 'fail').length} incorrect`
            }
          >
            {last_20_results.map((result, i) => (
              <div
                key={i}
                aria-hidden="true"
                className={`w-6 h-6 rounded-sm transition-colors ${
                  result === 'pass' ? 'bg-green-800/20' : 'bg-red-800/15'
                }`}
                title={result === 'pass' ? 'Correct' : 'Incorrect'}
              />
            ))}
          </div>
        </div>

        {/* Accuracy */}
        <div className="flex justify-between items-center pt-2">
          <span className="text-primary/60 font-sans text-sm">Accuracy</span>
          <span className="text-2xl font-mono text-primary">
            {Math.round(accuracy * 100)}%
          </span>
        </div>

        {/* Trend indicator (informational, not judgmental) */}
        <div className="flex items-center gap-2 text-sm">
          <span className="text-primary/60 font-sans">Trend:</span>
          <span className="text-primary/80 font-sans">
            {trendText}
          </span>
        </div>
      </div>
    </section>
  );
}
