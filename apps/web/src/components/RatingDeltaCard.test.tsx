import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { RatingDeltaCard } from './RatingDeltaCard';
import type { ExplainResponse } from '../api/ratings';

vi.mock('react-router-dom', () => ({
  Link: ({ children, to }: { children: React.ReactNode; to: string }) => <a href={to}>{children}</a>,
}));

const resp = (over: Partial<ExplainResponse>): ExplainResponse => ({
  time_control: 'rapid',
  window: { start: '', end: '', source: 'session' },
  rating: { start: 1500, end: 1518, net_change: 18, reference_rating: 1500, reference_is_approx: false },
  stats: { games: 12, wins: 7, draws: 2, losses: 3, avg_opponent_rating: 1510, expected_total: null, actual_total: null, actual_minus_expected: null, missing_opponent_rating_games: 0 },
  drivers: [], highlights: { best_surprises: [], worst_surprises: [] },
  chart_series: [{ at: 'a', rating: 1500, source: 'game' }, { at: 'b', rating: 1518, source: 'game' }],
  confidence: 'high', insufficient_data: false,
  ...over,
});

describe('RatingDeltaCard', () => {
  it('formats a positive delta and colours it (high confidence)', () => {
    render(<RatingDeltaCard data={resp({})} timeControlLabel="Rapid" />);
    const val = screen.getByText('+18');
    expect(val).toBeInTheDocument();
    expect(val).toHaveClass('text-positive');
    expect(screen.getByText('Rating · Rapid')).toBeInTheDocument();
  });

  it('does NOT colour a low-confidence delta (shown, but not asserted as fact)', () => {
    render(<RatingDeltaCard data={resp({ confidence: 'low' })} timeControlLabel="Rapid" />);
    const val = screen.getByText('+18');
    expect(val).not.toHaveClass('text-positive');
    expect(val).not.toHaveClass('text-negative');
    expect(screen.getByText(/Low confidence/)).toBeInTheDocument();
  });

  it('shows "—" and an explanation when there is no measurable change', () => {
    render(<RatingDeltaCard data={resp({ rating: { start: null, end: null, net_change: null, reference_rating: 0, reference_is_approx: false }, stats: { ...resp({}).stats, games: 0 } })} timeControlLabel="Rapid" />);
    expect(screen.getByText('—')).toBeInTheDocument();
    expect(screen.getByText(/Not enough games/)).toBeInTheDocument();
  });

  it('renders a sparkline when the series has enough points and links to details', () => {
    const { container } = render(<RatingDeltaCard data={resp({})} timeControlLabel="Rapid" />);
    expect(container.querySelector('svg polyline')).not.toBeNull();
    expect(screen.getByRole('link', { name: 'Details' })).toHaveAttribute('href', '/rating-insights');
  });
});
