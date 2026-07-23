interface SparklineProps {
  /** Series values, oldest→newest. Fewer than 2 points renders nothing. */
  points: number[];
  /** Line colour direction; drives the semantic positive/negative token. */
  trend?: 'up' | 'down';
  width?: number;
  height?: number;
  /**
   * Accessible label. Omit to render decoratively (aria-hidden) — appropriate
   * when an adjacent value already states the number the line reinforces.
   */
  ariaLabel?: string;
}

/**
 * Tiny dependency-free SVG sparkline. Deliberately not the full recharts
 * RatingChart — a stat-tile reinforcement line wants a few bytes and a single
 * stroke, not axes and tooltips. Themed via the positive/negative colour tokens
 * (stroke=currentColor) so it adapts to day/night like everything else.
 */
export function Sparkline({ points, trend = 'up', width = 96, height = 28, ariaLabel }: SparklineProps) {
  if (points.length < 2) return null;

  const min = Math.min(...points);
  const max = Math.max(...points);
  const span = max - min || 1; // flat series → avoid divide-by-zero
  const pad = 2;
  const innerW = width - pad * 2;
  const innerH = height - pad * 2;

  const coords = points.map((v, i) => {
    const x = pad + (i / (points.length - 1)) * innerW;
    // Invert Y: a higher value sits higher on screen (smaller y).
    const y = pad + (1 - (v - min) / span) * innerH;
    return `${x.toFixed(2)},${y.toFixed(2)}`;
  });

  const colorClass = trend === 'down' ? 'text-negative' : 'text-positive';

  return (
    <svg
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      className={colorClass}
      role={ariaLabel ? 'img' : undefined}
      aria-label={ariaLabel}
      aria-hidden={ariaLabel ? undefined : true}
      preserveAspectRatio="none"
    >
      <polyline
        points={coords.join(' ')}
        fill="none"
        stroke="currentColor"
        strokeWidth={1.5}
        strokeLinejoin="round"
        strokeLinecap="round"
      />
      {/* Emphasise the latest point so the line has a clear "now". */}
      <circle
        cx={coords[coords.length - 1].split(',')[0]}
        cy={coords[coords.length - 1].split(',')[1]}
        r={2}
        fill="currentColor"
      />
    </svg>
  );
}
