export type TimeControl = 'rapid' | 'blitz' | 'bullet';

/** Display labels for the three tracked time controls. */
export const TC_LABEL: Record<TimeControl, string> = {
  rapid: 'Rapid',
  blitz: 'Blitz',
  bullet: 'Bullet',
};

/**
 * Format a signed number for a delta display: a leading "+" for positive values,
 * the bare value otherwise. Pass `digits` to fix decimals (e.g. score deltas);
 * omit it to preserve the number's natural string form (e.g. integer ratings).
 */
export function formatSigned(n: number, digits?: number): string {
  const s = digits === undefined ? String(n) : n.toFixed(digits);
  return n > 0 ? `+${s}` : s;
}
