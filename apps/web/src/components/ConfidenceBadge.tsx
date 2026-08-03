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

/**
 * Shown when the server sends a level this build has no entry for. Neutral ink
 * rather than one of the three semantic tokens: the badge's whole meaning is a
 * position on a 3-point scale, so an unrecognised level must not be dressed up
 * as one of them. Full-strength ink because 10px text needs the contrast.
 */
const UNKNOWN_BADGE = { label: 'Confidence unavailable', color: 'bg-primary/10 text-primary' };

/**
 * `confidence` is declared as a closed union but arrives unvalidated from the
 * server, so this treats it as `unknown` — which is what it actually is.
 *
 * `Object.hasOwn`, not a nullish check on the lookup: `BADGE` inherits from
 * Object.prototype, so a level of "constructor" or "toString" returns an
 * inherited *function*, which is not nullish — `badge.color` then silently
 * yields the class string "undefined" and renders an invisible badge.
 * Everything else (unknown tier, missing field, null, '', 0, wrong case)
 * previously threw on `.color` and took the whole app down via the root
 * ErrorBoundary — nav included.
 */
function badgeFor(confidence: Confidence): { label: string; color: string } {
  const raw: unknown = confidence;
  if (typeof raw === 'string' && Object.hasOwn(BADGE, raw)) {
    return BADGE[raw as Confidence];
  }
  return UNKNOWN_BADGE;
}

export function ConfidenceBadge({ confidence, games }: ConfidenceBadgeProps) {
  const badge = badgeFor(confidence);
  return (
    <span className={`text-[10px] font-sans font-medium px-2 py-0.5 rounded-full ${badge.color}`}>
      {badge.label}{games != null ? ` (${games} game${games === 1 ? '' : 's'})` : ''}
    </span>
  );
}
