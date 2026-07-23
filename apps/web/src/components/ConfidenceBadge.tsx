export type Confidence = 'low' | 'medium' | 'high';

interface ConfidenceBadgeProps {
  confidence: Confidence;
  /** When set, appended as "(N games)" — the sample the confidence is drawn from. */
  games?: number;
}

// Shared with Rating Insights so the same uncertainty signal reads identically
// wherever it appears. Colour + wording both encode the level (never colour
// alone); the low tier deliberately uses the negative token to read as "treat
// this cautiously".
const BADGE: Record<Confidence, { label: string; color: string }> = {
  low: { label: 'Low confidence', color: 'bg-negative-soft text-negative' },
  medium: { label: 'Medium confidence', color: 'bg-status-learning-soft text-status-learning' },
  high: { label: 'High confidence', color: 'bg-positive-soft text-positive' },
};

export function ConfidenceBadge({ confidence, games }: ConfidenceBadgeProps) {
  const badge = BADGE[confidence];
  return (
    <span className={`text-[10px] font-sans font-medium px-2 py-0.5 rounded-full ${badge.color}`}>
      {badge.label}{games != null ? ` (${games} game${games === 1 ? '' : 's'})` : ''}
    </span>
  );
}
