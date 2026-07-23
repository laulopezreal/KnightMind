import { Link } from 'react-router-dom';
import { StatCard } from './StatCard';
import { ConfidenceBadge } from './ConfidenceBadge';
import { Sparkline } from './Sparkline';
import { formatSigned } from '../utils/ratings';
import type { ExplainResponse } from '../api/ratings';

interface RatingDeltaCardProps {
  data: ExplainResponse;
  timeControlLabel: string;
}

/**
 * The "is my training working?" tile: net rating change over the window, drawn
 * from real games, with a sparkline and a confidence flag. Inherits the app's
 * calibration discipline — a low-confidence delta is shown in neutral ink (not
 * a confident green/red), and a window without both anchors reads "—" rather
 * than a fabricated number.
 */
export function RatingDeltaCard({ data, timeControlLabel }: RatingDeltaCardProps) {
  const net = data.rating.net_change;
  const confidence = data.confidence;
  const games = data.stats.games;
  const hasDelta = net != null;
  // Only colour the number when we're actually confident in it.
  const confidentDelta = hasDelta && net !== 0 && confidence !== 'low';

  const points = (data.chart_series ?? data.trajectory ?? []).map((p) => p.rating);
  const trend: 'up' | 'down' = hasDelta && net < 0 ? 'down' : 'up';

  const value = hasDelta ? formatSigned(net) : '—';
  const sub = hasDelta && data.rating.start != null && data.rating.end != null
    ? `${data.rating.start} → ${data.rating.end}`
    : games > 0
    ? `${games} rated game${games === 1 ? '' : 's'} in window`
    : 'Not enough games to measure a change.';

  return (
    <StatCard
      label={`Rating · ${timeControlLabel}`}
      value={value}
      highlight={confidentDelta}
      positive={hasDelta && net > 0}
      sub={sub}
      footer={
        <div className="flex items-center justify-between gap-3">
          <div className="flex items-center gap-2">
            <ConfidenceBadge confidence={confidence} games={games} />
            {points.length >= 2 && (
              <Sparkline points={points} trend={trend} ariaLabel={`Rating trend, ${points.length} games`} />
            )}
          </div>
          <Link
            to="/rating-insights"
            className="km-interactive km-focus-visible km-inline-link text-primary text-xs font-medium underline decoration-primary/30 underline-offset-4 transition-colors shrink-0"
          >
            Details
          </Link>
        </div>
      }
    />
  );
}
