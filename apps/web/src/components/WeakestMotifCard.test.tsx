import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { WeakestMotifCard } from './WeakestMotifCard';
import type { MotifPerformance } from '../api/users';

vi.mock('react-router-dom', () => ({
  Link: ({ children, to }: { children: React.ReactNode; to: string }) => <a href={to}>{children}</a>,
}));

const motif = (over: Partial<MotifPerformance>): MotifPerformance => ({
  name: 'fork', total_puzzles: 10, passed: 8, accuracy: 0.8,
  rank: 'learning', attempts: 10, insufficient_data: false, ...over,
});

describe('WeakestMotifCard', () => {
  it('picks the lowest-accuracy RELIABLE motif and humanises its name', () => {
    render(<WeakestMotifCard motifs={[
      motif({ name: 'fork', accuracy: 0.8 }),
      motif({ name: 'back_rank', accuracy: 0.39, rank: 'needs_work' }),
      // Lower accuracy but unreliable — must be ignored, not chosen.
      motif({ name: 'skewer', accuracy: 0.2, insufficient_data: true }),
    ]} />);

    expect(screen.getByText('Back Rank')).toBeInTheDocument();
    expect(screen.getByText(/39% · Needs work/)).toBeInTheDocument();
    expect(screen.queryByText('Skewer')).not.toBeInTheDocument();
  });

  it('deep-links "Train this" to the raw motif key', () => {
    render(<WeakestMotifCard motifs={[motif({ name: 'back_rank', accuracy: 0.39 })]} />);
    expect(screen.getByRole('link', { name: 'Train this' })).toHaveAttribute('href', '/puzzles?motif=back_rank');
  });

  it('shows a "not enough data" state when no motif is reliable', () => {
    render(<WeakestMotifCard motifs={[motif({ insufficient_data: true, accuracy: 0.2 })]} />);
    expect(screen.getByText(/Not enough attempts/)).toBeInTheDocument();
    expect(screen.queryByRole('link', { name: 'Train this' })).not.toBeInTheDocument();
  });

  it('celebrates when every reliable motif is above the mastery bar', () => {
    render(<WeakestMotifCard motifs={[
      motif({ name: 'fork', accuracy: 0.9 }),
      motif({ name: 'pin', accuracy: 0.88 }),
    ]} />);
    expect(screen.getByText('All strong')).toBeInTheDocument();
    expect(screen.queryByRole('link', { name: 'Train this' })).not.toBeInTheDocument();
  });
});
