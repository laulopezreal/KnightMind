import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import type { AnchorHTMLAttributes } from 'react';
import { WeakestMotifCard } from './WeakestMotifCard';
import type { MotifPerformance } from '../api/users';

vi.mock('react-router-dom', () => ({
  Link: ({ children, to, ...props }: AnchorHTMLAttributes<HTMLAnchorElement> & { to: string }) => (
    <a href={to} {...props}>{children}</a>
  ),
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

  // `rank` is a closed union in the frontend's types but an unvalidated server
  // field at runtime, so every cast below stands in for a payload the type
  // system cannot rule out. All were reproduced against the running app.
  const asRank = (v: unknown) => v as MotifPerformance['rank'];

  it('falls back to the tier name when the server sends an unknown rank', () => {
    render(<WeakestMotifCard motifs={[
      motif({ name: 'back_rank', accuracy: 0.13, rank: asRank('critical') }),
    ]} />);

    expect(screen.getByText('13% · Critical')).toBeInTheDocument();
    expect(screen.queryByText(/undefined/)).not.toBeInTheDocument();
  });

  it('humanises an unknown snake_case tier so it matches the motif name beside it', () => {
    render(<WeakestMotifCard motifs={[
      motif({ name: 'back_rank', accuracy: 0.13, rank: asRank('severely_needs_work') }),
    ]} />);
    expect(screen.getByText('13% · Severely Needs Work')).toBeInTheDocument();
  });

  it.each([
    ['missing', undefined],
    ['null', null],
    ['empty string', ''],
    ['whitespace', '   '],
    ['a number', 0],
  ])('drops the separator instead of rendering a %s rank', (_label, value) => {
    // The naive `RANK_LABEL[rank] ?? rank` is circular here: the fallback
    // returns the same nullish value, so the tile rendered "13% · undefined".
    render(<WeakestMotifCard motifs={[
      motif({ name: 'back_rank', accuracy: 0.13, rank: asRank(value) }),
    ]} />);

    expect(screen.getByText('13%')).toBeInTheDocument();
    expect(screen.queryByText(/undefined|null|·/)).not.toBeInTheDocument();
  });

  it.each(['constructor', 'toString', 'valueOf'])(
    'does not leak the prototype member %s as a label',
    (proto) => {
      // A bare lookup finds these on Object.prototype and returns a *function*,
      // which is not nullish — so `??` never fires and the tile rendered
      // "13% · function Object() { [native code] }".
      render(<WeakestMotifCard motifs={[
        motif({ name: 'back_rank', accuracy: 0.13, rank: asRank(proto) }),
      ]} />);

      expect(screen.queryByText(/native code|function/)).not.toBeInTheDocument();
      expect(screen.getByText(`13% · ${proto[0].toUpperCase()}${proto.slice(1)}`)).toBeInTheDocument();
    },
  );

  it('caps an over-long unknown tier so it cannot force horizontal page scroll', () => {
    render(<WeakestMotifCard motifs={[
      motif({
        name: 'back_rank', accuracy: 0.13,
        rank: asRank('extremely_critical_needs_immediate_and_sustained_remedial_attention'),
      }),
    ]} />);

    const sub = screen.getByText(/^13% · /);
    expect(sub.textContent!.length).toBeLessThanOrEqual('13% · '.length + 24);
    expect(sub.textContent).toMatch(/…$/);
  });

  it('deep-links "Train this" to the raw motif key', () => {
    render(<WeakestMotifCard motifs={[motif({ name: 'back_rank', accuracy: 0.39 })]} />);
    expect(screen.getByRole('link', { name: 'Train this' })).toHaveAttribute('href', '/puzzles?motif=back_rank');
  });

  it('keeps the secondary motifs link at least 44px tall', () => {
    render(<WeakestMotifCard motifs={[motif({ name: 'back_rank', accuracy: 0.39 })]} />);
    expect(screen.getByRole('link', { name: 'See all motifs' })).toHaveClass('min-h-11');
  });

  it('keeps the diagnosis but removes its competing training action when a daily focus exists', () => {
    render(<WeakestMotifCard motifs={[motif({ name: 'back_rank', accuracy: 0.39 })]} trainingEnabled={false} />);

    expect(screen.getByText('Back Rank')).toBeInTheDocument();
    expect(screen.queryByRole('link', { name: 'Train this' })).not.toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'See all motifs' })).toBeInTheDocument();
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
