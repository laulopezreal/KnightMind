import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { ConfidenceBadge } from './ConfidenceBadge';

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
});
