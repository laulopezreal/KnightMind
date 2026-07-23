import type { MotifPerformance } from '../api/users';

/** Accuracy at/above which a motif counts as mastered (not a "weakness"). */
export const MOTIF_MASTERY_THRESHOLD = 0.85;

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

/**
 * Weakest reliable motif and whether every reliable motif is mastered. Only
 * motifs with enough attempts (`!insufficient_data`) count, so one unlucky
 * puzzle is never a "weakness". Shared by the Insights radar's logic, the
 * dashboard's weakest-motif card, and the smart-hero shortcut.
 */
export function weakestMotif(motifs: MotifPerformance[]): {
  weakest: MotifPerformance | null;
  allStrong: boolean;
} {
  const reliable = motifs.filter((m) => !m.insufficient_data);
  const weakest = reliable.length
    ? reliable.reduce((min, m) => (m.accuracy < min.accuracy ? m : min))
    : null;
  const allStrong =
    reliable.length > 0 && reliable.every((m) => m.accuracy >= MOTIF_MASTERY_THRESHOLD);
  return { weakest, allStrong };
}
