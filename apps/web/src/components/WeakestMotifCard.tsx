import { Link } from 'react-router-dom';
import { StatCard } from './StatCard';
import { formatMotifName } from '../utils/motif';
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
 * Surfaces the user's weakest tactical motif from their own games — the single
 * most actionable, app-specific signal — with a one-click targeted-training
 * deep link. Mirrors the weakest-selection rule used by the Insights radar:
 * only motifs with enough attempts count, so one unlucky puzzle is never a
 * "weakness".
 */
export function WeakestMotifCard({ motifs }: WeakestMotifCardProps) {
  const reliable = motifs.filter((m) => !m.insufficient_data);
  const weakest = reliable.length
    ? reliable.reduce((min, m) => (m.accuracy < min.accuracy ? m : min))
    : null;
  const allStrong = reliable.length > 0 && reliable.every((m) => m.accuracy >= 0.85);

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
      sub={`${Math.round(weakest.accuracy * 100)}% · ${RANK_LABEL[weakest.rank]}`}
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
