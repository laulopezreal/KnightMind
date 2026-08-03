import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { ConfidenceBadge } from './ConfidenceBadge';
import type { Confidence } from './ConfidenceBadge';

describe('ConfidenceBadge', () => {
  it('labels each level (never colour alone)', () => {
    const { rerender } = render(<ConfidenceBadge confidence="low" />);
    expect(screen.getByText('Low confidence')).toBeInTheDocument();
    rerender(<ConfidenceBadge confidence="medium" />);
    expect(screen.getByText('Medium confidence')).toBeInTheDocument();
    rerender(<ConfidenceBadge confidence="high" />);
    expect(screen.getByText('High confidence')).toBeInTheDocument();
  });

  it('appends the game sample when provided, singularising 1', () => {
    const { rerender } = render(<ConfidenceBadge confidence="high" games={12} />);
    expect(screen.getByText('High confidence (12 games)')).toBeInTheDocument();
    rerender(<ConfidenceBadge confidence="high" games={1} />);
    expect(screen.getByText('High confidence (1 game)')).toBeInTheDocument();
  });

  it('uses the negative token for low confidence (read as "treat cautiously")', () => {
    render(<ConfidenceBadge confidence="low" />);
    expect(screen.getByText('Low confidence')).toHaveClass('text-negative');
  });

  // `confidence` is a closed union in the frontend's types but an unvalidated
  // server field at runtime. Every cast below stands in for a payload the type
  // system cannot rule out; all were reproduced against the running app, where
  // they took out the whole page (nav included) via the root ErrorBoundary.
  const asConfidence = (v: unknown) => v as Confidence;

  it.each([
    ['an unknown level', 'very_high'],
    ['a missing field', undefined],
    ['null', null],
    ['an empty string', ''],
    ['a number', 0],
    ['the right level in the wrong case', 'HIGH'],
  ])('degrades to a neutral badge for %s instead of crashing', (_label, value) => {
    expect(() =>
      render(<ConfidenceBadge confidence={asConfidence(value)} games={30} />),
    ).not.toThrow();

    const badge = screen.getByText('Confidence unavailable (30 games)');
    expect(badge).toBeInTheDocument();
    // Neutral ink: an uninterpretable level must not borrow a semantic token.
    expect(badge.className).not.toMatch(/text-(negative|positive|status-learning)/);
    expect(badge).toHaveClass('text-primary');
  });

  it.each(['constructor', 'toString', 'valueOf'])(
    'does not render an invisible badge for the prototype member %s',
    (proto) => {
      // These resolve through Object.prototype to inherited *functions*, so a
      // nullish check never fires: `badge.color` was `undefined`, producing a
      // span with the literal class "undefined" and no label at all.
      render(<ConfidenceBadge confidence={asConfidence(proto)} />);

      const badge = screen.getByText('Confidence unavailable');
      expect(badge).toBeInTheDocument();
      expect(badge.className).not.toMatch(/undefined/);
    },
  );

  it('keeps the sample size visible when the level is unrecognised', () => {
    // The game count is independent evidence — losing it would make a degraded
    // badge less informative than it needs to be.
    render(<ConfidenceBadge confidence={asConfidence('very_high')} games={1} />);
    expect(screen.getByText('Confidence unavailable (1 game)')).toBeInTheDocument();
  });
});
