/**
 * Humanise a raw `primary_motif` key (snake_case, e.g. "back_rank") for display.
 * The raw key is what the backend filters on, so it stays untouched in deep
 * links — only the shown label is prettified.
 */
export function formatMotifName(raw: string): string {
  return raw
    .split('_')
    .map((w) => (w ? w[0].toUpperCase() + w.slice(1) : w))
    .join(' ');
}
