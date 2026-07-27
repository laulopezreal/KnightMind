import type { ReactNode } from 'react';

interface StatCardProps {
  label: string;
  value: string;
  sub?: string;
  helper?: string;
  /** Colours the value with the positive/negative token instead of ink. */
  highlight?: boolean;
  positive?: boolean;
  extra?: string;
  /** Optional slot under the stat — a sparkline, deep-link, or badge row. */
  footer?: ReactNode;
}

/**
 * Shared stat tile used across Rating Insights and the Dashboard improvement
 * strip. Promoted from a page-private component so the two surfaces render
 * identically rather than via lookalike re-implementations. Purely
 * presentational; only the optional `footer` slot is new relative to the
 * original.
 */
export function StatCard({ label, value, sub, helper, highlight, positive, extra, footer }: StatCardProps) {
  return (
    <div className="p-6 bg-primary/5 rounded-sm border border-primary/10">
      <div className="text-xs font-sans uppercase tracking-widest text-primary/70 mb-2">{label}</div>
      <div className={`text-3xl font-serif mb-1 ${highlight ? (positive ? 'text-positive' : 'text-negative') : 'text-primary'}`}>
        {value}
      </div>
      {sub && <div className="text-xs font-sans text-primary/70 mb-1">{sub}</div>}
      {helper && <div className="text-xs font-sans text-primary/70 italic">{helper}</div>}
      {extra && <div className="text-xs font-sans text-primary/70 mt-2">{extra}</div>}
      {footer && <div className="mt-4">{footer}</div>}
    </div>
  );
}
