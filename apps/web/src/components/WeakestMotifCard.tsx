import { Link } from 'react-router-dom';
import { StatCard } from './StatCard';
import { formatMotifName, weakestMotif } from '../utils/motif';
import type { MotifPerformance } from '../api/users';

interface WeakestMotifCardProps {
  motifs: MotifPerformance[];
}

const RANK_LABEL: Record<MotifPerformance['rank'], string> = {
  needs_work: 'Needs work',
  learning: 'Learning',
  mastered: 'Mastered',
};

/**
 * `rank` is typed as a closed union but arrives from the server, so a tier the
 * frontend doesn't know about is possible at runtime. The cast widens the key
 * type only at the point of read — the map keeps its exhaustive declaration, so
 * adding a tier to the union still fails the build until a label exists — while
 * making the fallback live rather than dead code. Showing the raw tier name
 * beats rendering the string "undefined" at the user.
 */
function rankLabel(rank: MotifPerformance['rank']): string {
  return (RANK_LABEL as Record<string, string | undefined>)[rank] ?? rank;
}

/**
 * Surfaces the user's weakest tactical motif from their own games — the single
 * most actionable, app-specific signal — with a one-click targeted-training
 * deep link. Mirrors the weakest-selection rule used by the Insights radar:
 * only motifs with enough attempts count, so one unlucky puzzle is never a
 * "weakness".
 */
export function WeakestMotifCard({ motifs }: WeakestMotifCardProps) {
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
      sub={`${Math.round(weakest.accuracy * 100)}% · ${rankLabel(weakest.rank)}`}
      footer={
        <div className="flex items-center gap-4">
          <Link
            to={`/puzzles?motif=${encodeURIComponent(weakest.name)}`}
            className="km-interactive km-focus-visible text-sm font-serif px-4 py-1.5 bg-primary text-bg-primary rounded-sm transition-opacity hover:opacity-90"
          >
            Train this
          </Link>
          {insightsLink}
        </div>
      }
    />
  );
}
