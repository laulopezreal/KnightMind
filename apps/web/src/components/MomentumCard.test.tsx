import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MomentumCard } from './MomentumCard';

describe('MomentumCard', () => {
  const defaultForm = {
    last_20_results: Array(10).fill('pass').concat(Array(10).fill('fail')) as ('pass' | 'fail')[],
    accuracy: 0.5,
    trend: 'steady' as const,
    sample_size: 20,
    insufficient_data: false,
  };

  it('should render Momentum heading', () => {
    render(<MomentumCard recentForm={defaultForm} />);

    expect(screen.getByText('Momentum')).toBeInTheDocument();
  });

  it('should display accuracy percentage', () => {
    render(<MomentumCard recentForm={defaultForm} />);

    expect(screen.getByText('50%')).toBeInTheDocument();
  });

  it('should display "Steady" trend', () => {
    render(<MomentumCard recentForm={defaultForm} />);

    expect(screen.getByText('Steady')).toBeInTheDocument();
  });

  it('should display "Improving" for upward trend', () => {
    render(<MomentumCard recentForm={{ ...defaultForm, trend: 'up' }} />);

    expect(screen.getByText('Improving')).toBeInTheDocument();
  });

  it('should display "Slight dip" for downward trend', () => {
    render(<MomentumCard recentForm={{ ...defaultForm, trend: 'down' }} />);

    expect(screen.getByText('Slight dip')).toBeInTheDocument();
  });

  it('should render result indicators with correct titles', () => {
    render(<MomentumCard recentForm={defaultForm} />);

    const correct = screen.getAllByTitle('Correct');
    const incorrect = screen.getAllByTitle('Incorrect');

    expect(correct).toHaveLength(10);
    expect(incorrect).toHaveLength(10);
  });

  it('should have proper aria-labelledby', () => {
    render(<MomentumCard recentForm={defaultForm} />);

    const section = screen.getByRole('region', { name: /momentum/i });
    expect(section).toBeInTheDocument();
  });

  it('summarises the results row as a single labelled image', () => {
    render(<MomentumCard recentForm={defaultForm} />);

    // One img with an aggregate label, instead of 20 individually-labelled divs.
    const summary = screen.getByRole('img', { name: /20 puzzles: 10 correct, 10 incorrect/i });
    expect(summary).toBeInTheDocument();
  });

  it('shows "Not enough data yet" when the sample is too small for a trend', () => {
    render(<MomentumCard recentForm={{ ...defaultForm, trend: 'steady', sample_size: 4, insufficient_data: true }} />);

    expect(screen.getByText('Not enough data yet')).toBeInTheDocument();
    // The generic "Steady" label must not imply a settled trend here.
    expect(screen.queryByText('Steady')).not.toBeInTheDocument();
  });

  it('handles empty recent results gracefully', () => {
    render(<MomentumCard recentForm={{ ...defaultForm, last_20_results: [], sample_size: 0, insufficient_data: true }} />);

    const summary = screen.getByRole('img', { name: /no recent attempts yet/i });
    expect(summary).toBeInTheDocument();
    // Heading avoids the awkward "Last 0 puzzles".
    expect(screen.getByText('Recent puzzles')).toBeInTheDocument();
  });
});
