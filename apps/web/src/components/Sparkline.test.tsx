import { describe, it, expect } from 'vitest';
import { render } from '@testing-library/react';
import { Sparkline } from './Sparkline';

describe('Sparkline', () => {
  it('renders nothing with fewer than two points', () => {
    const { container } = render(<Sparkline points={[1500]} />);
    expect(container.querySelector('svg')).toBeNull();
  });

  it('draws a polyline through the series', () => {
    const { container } = render(<Sparkline points={[1500, 1490, 1520]} />);
    const poly = container.querySelector('polyline');
    expect(poly).not.toBeNull();
    // 3 points → 3 coordinate pairs.
    expect(poly!.getAttribute('points')!.trim().split(' ')).toHaveLength(3);
  });

  it('uses the negative colour token for a downward trend', () => {
    const { container } = render(<Sparkline points={[1520, 1500]} trend="down" />);
    expect(container.querySelector('svg')).toHaveClass('text-negative');
  });

  it('uses neutral ink for a flat trend (no asserted direction)', () => {
    // Steady/insufficient-data series must not read as green wins.
    const { container } = render(<Sparkline points={[1500, 1510, 1490]} trend="flat" />);
    const svg = container.querySelector('svg');
    expect(svg).toHaveClass('text-primary/60');
    expect(svg).not.toHaveClass('text-positive');
  });

  it('is decorative (aria-hidden) without a label, labelled img with one', () => {
    const { container, rerender } = render(<Sparkline points={[1, 2]} />);
    expect(container.querySelector('svg')).toHaveAttribute('aria-hidden', 'true');
    rerender(<Sparkline points={[1, 2]} ariaLabel="Rating trend" />);
    expect(container.querySelector('svg')).toHaveAttribute('role', 'img');
    expect(container.querySelector('svg')).toHaveAttribute('aria-label', 'Rating trend');
  });
});
