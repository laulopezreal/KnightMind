import type { RecentFormData } from '../api/users';

interface MomentumCardProps {
  recentForm: RecentFormData;
}

export function MomentumCard({ recentForm }: MomentumCardProps) {
  const { last_20_results, accuracy, trend, insufficient_data } = recentForm;

  // Map trend to informational text (not judgmental). With too few reviews the
  // direction is not meaningful, so we say so rather than imply a trend.
  // The glyph is a glanceable, colour-independent direction cue (aria-hidden —
  // the text carries the meaning for screen readers); the colour is a secondary
  // reinforcement drawn from theme tokens so it adapts to day/night.
  const trendMeta = insufficient_data
    ? { text: 'Not enough data yet', glyph: null, color: 'text-primary/80' }
    : trend === 'up'
    ? { text: 'Improving', glyph: '↗', color: 'text-positive' }
    : trend === 'down'
    ? { text: 'Slight dip', glyph: '↘', color: 'text-negative' }
    : { text: 'Steady', glyph: '→', color: 'text-primary/80' };

  return (
    <section
      className="bg-primary/5 border border-primary/10 rounded-sm p-6"
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
          <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1 text-xs text-primary/70 font-sans mb-2">
            <span>{last_20_results.length > 0 ? `Last ${last_20_results.length} puzzles` : 'Recent puzzles'}</span>
            <span aria-hidden="true">✓ Correct · × Incorrect</span>
          </div>
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
                className={`w-6 h-6 rounded-sm transition-colors flex items-center justify-center text-sm font-bold leading-none text-primary ${
                  result === 'pass'
                    ? 'bg-positive-fill'
                    : 'bg-negative-fill border border-primary/20'
                }`}
                title={result === 'pass' ? 'Correct' : 'Incorrect'}
              >
                {result === 'pass' ? '✓' : '×'}
              </div>
            ))}
          </div>
        </div>

        {/* Accuracy. Labelled "Review accuracy" (not a bare "Accuracy") to be
            honest about what it measures: recall on your spaced-repetition deck,
            which is shaped by what the scheduler served — not a direct chess-skill
            score. The rating tile on the dashboard carries the skill/outcome signal. */}
        <div className="flex justify-between items-center pt-2">
          <span className="text-primary/70 font-sans text-sm">Review accuracy</span>
          <span className="text-2xl font-mono text-primary">
            {Math.round(accuracy * 100)}%
          </span>
        </div>

        {/* Trend indicator (informational, not judgmental) */}
        <div className="flex items-center gap-2 text-sm">
          <span className="text-primary/70 font-sans">Trend:</span>
          <span className={`font-sans flex items-center gap-1 ${trendMeta.color}`}>
            {trendMeta.glyph && (
              <span aria-hidden="true" className="text-base leading-none">{trendMeta.glyph}</span>
            )}
            {trendMeta.text}
          </span>
        </div>

        <p className="text-xs text-primary/70 font-sans italic pt-1">
          Recall on puzzles you've reviewed — not a chess-skill score.
        </p>
      </div>
    </section>
  );
}
