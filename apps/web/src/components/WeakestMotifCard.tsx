import { Link } from 'react-router-dom';
import { StatCard } from './StatCard';
import { formatMotifName, weakestMotif } from '../utils/motif';
import type { MotifPerformance } from '../api/users';

interface WeakestMotifCardProps {
  motifs: MotifPerformance[];
  /** Suppress the generic motif route when Today's Focus owns the priority path. */
  trainingEnabled?: boolean;
}

const RANK_LABEL: Record<MotifPerformance['rank'], string> = {
  needs_work: 'Needs work',
  learning: 'Learning',
  mastered: 'Mastered',
};

/**
 * Longest unrecognised tier we echo. Real tier keys are short; a long one is
 * unbroken snake_case, which has no wrap opportunity and pushes the whole page
 * into horizontal scroll on mobile.
 */
const MAX_RAW_RANK = 24;

/**
 * Label for a rank tier, or null when there is no tier worth showing.
 *
 * `rank` is declared as a closed union but arrives unvalidated from the server,
 * so this treats it as `unknown` — which is what it actually is. Three rules,
 * each learned from a payload that broke the naive version:
 *
 * 1. Membership is tested with `Object.hasOwn`, not a nullish check on the
 *    lookup. `RANK_LABEL` inherits from Object.prototype, so a rank of
 *    "constructor" or "toString" returns an inherited *function* — not nullish,
 *    so `??` never fires and the tile renders "function Object() { … }".
 * 2. Anything unrecognised is echoed only if it is a usable non-empty string.
 *    Falling back to the raw value itself (`RANK_LABEL[rank] ?? rank`) is
 *    circular: when the field is missing the fallback returns `undefined` too,
 *    reproducing the exact "13% · undefined" this is meant to prevent.
 * 3. The echo is humanised and length-capped so an unknown tier reads like the
 *    motif name beside it instead of raw snake_case, and cannot break layout.
 */
function rankLabel(rank: MotifPerformance['rank']): string | null {
  const raw: unknown = rank;
  if (typeof raw === 'string' && Object.hasOwn(RANK_LABEL, raw)) {
    return RANK_LABEL[raw as MotifPerformance['rank']];
  }
  if (typeof raw !== 'string' || !raw.trim()) return null;
  const pretty = formatMotifName(raw.trim());
  return pretty.length > MAX_RAW_RANK ? `${pretty.slice(0, MAX_RAW_RANK - 1)}…` : pretty;
}

/**
 * "39% · Needs work", or just "39%" when the server sent no usable tier — a
 * dangling "39% · " separator reads as a rendering failure.
 */
function subLine(weakest: MotifPerformance): string {
  const label = rankLabel(weakest.rank);
  const pct = `${Math.round(weakest.accuracy * 100)}%`;
  return label ? `${pct} · ${label}` : pct;
}

/**
 * Surfaces the user's weakest tactical motif from their own games — the single
 * most actionable, app-specific signal — with a one-click targeted-training
 * deep link. Mirrors the weakest-selection rule used by the Insights radar:
 * only motifs with enough attempts count, so one unlucky puzzle is never a
 * "weakness".
 */
export function WeakestMotifCard({ motifs, trainingEnabled = true }: WeakestMotifCardProps) {
  const { weakest, allStrong } = weakestMotif(motifs);

  const insightsLink = (
    <Link
      to="/insights"
      className="km-interactive km-focus-visible km-inline-link text-primary text-xs font-medium underline decoration-primary/30 underline-offset-4 transition-colors"
    >
      See all motifs
    </Link>
  );

  // Not enough reliable data yet — say so honestly, point at the deeper page.
  if (!weakest) {
    return (
      <StatCard
        label="Weakest motif"
        value="—"
        sub="Not enough attempts yet to name a weakest area."
        footer={insightsLink}
      />
    );
  }

  // Everything reliable is above the mastery bar — celebrate rather than invent
  // a "weakest" from strong numbers.
  if (allStrong) {
    return (
      <StatCard
        label="Weakest motif"
        value="All strong"
        positive
        highlight
        sub="Every practised motif is above 85%."
        footer={insightsLink}
      />
    );
  }

  return (
    <StatCard
      label="Weakest motif"
      value={formatMotifName(weakest.name)}
      sub={subLine(weakest)}
      footer={
        <div className="flex items-center gap-4">
          {trainingEnabled && (
            <Link
              to={`/puzzles?motif=${encodeURIComponent(weakest.name)}`}
              className="km-interactive km-focus-visible inline-flex min-h-11 items-center text-sm font-serif px-4 py-2 bg-primary text-bg-primary rounded-sm transition-opacity hover:opacity-90"
            >
              Train this
            </Link>
          )}
          {insightsLink}
        </div>
      }
    />
  );
}
