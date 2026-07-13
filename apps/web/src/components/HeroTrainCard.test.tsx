import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { HeroTrainCard } from './HeroTrainCard';

vi.mock('../utils/time', () => ({
  formatRelativeTime: (iso: string | null) => iso ? 'in 2h' : 'N/A',
}));

describe('HeroTrainCard', () => {
  const user = userEvent.setup();
  const defaultProps = {
    dueCount: 5,
    nextReviewAt: null,
    needsWarmup: false,
    daysSinceLastSession: 0,
    totalSessions: 10,
    onStartSession: vi.fn(),
  };

  it('should display due count', () => {
    render(<HeroTrainCard {...defaultProps} />);

    expect(screen.getByText('5')).toBeInTheDocument();
    expect(screen.getByText('puzzles due')).toBeInTheDocument();
  });

  it('should show "Train Today" title for normal state', () => {
    render(<HeroTrainCard {...defaultProps} />);

    expect(screen.getByText('Train Today')).toBeInTheDocument();
  });

  it('should show "Ready to Start Training?" for first-time users', () => {
    render(<HeroTrainCard {...defaultProps} totalSessions={0} />);

    expect(screen.getByText('Ready to Start Training?')).toBeInTheDocument();
    expect(screen.getByText('Start First Session')).toBeInTheDocument();
  });

  it('should show "Welcome Back" for warmup state', () => {
    render(<HeroTrainCard {...defaultProps} needsWarmup={true} daysSinceLastSession={5} />);

    expect(screen.getByText('Welcome Back')).toBeInTheDocument();
    expect(screen.getByText('Start Warmup (5 puzzles)')).toBeInTheDocument();
  });

  it('should show "All Caught Up" when no puzzles due', () => {
    render(<HeroTrainCard {...defaultProps} dueCount={0} />);

    expect(screen.getByText('All Caught Up')).toBeInTheDocument();
    expect(screen.getByText('Browse Puzzles')).toBeInTheDocument();
  });

  it('should call onStartSession when button is clicked', async () => {
    const onStartSession = vi.fn();
    render(<HeroTrainCard {...defaultProps} onStartSession={onStartSession} />);

    await user.click(screen.getByText('Start Session'));
    expect(onStartSession).toHaveBeenCalledTimes(1);
  });

  it('should show next review time when provided', () => {
    render(<HeroTrainCard {...defaultProps} nextReviewAt="2025-01-15T14:00:00Z" />);

    expect(screen.getByText(/Next review:/)).toBeInTheDocument();
  });

  it('should not show next review when null', () => {
    render(<HeroTrainCard {...defaultProps} nextReviewAt={null} />);

    expect(screen.queryByText(/Next review:/)).not.toBeInTheDocument();
  });

  it('should use singular "puzzle" for dueCount of 1', () => {
    render(<HeroTrainCard {...defaultProps} dueCount={1} />);

    expect(screen.getByText('puzzle due')).toBeInTheDocument();
  });

  it('should have proper aria-labelledby', () => {
    render(<HeroTrainCard {...defaultProps} />);

    const section = screen.getByRole('region', { name: /train today/i });
    expect(section).toBeInTheDocument();
  });

  it('renders the CTA with a solid fill, not the no-op bg-accent utility', () => {
    render(<HeroTrainCard {...defaultProps} />);

    // bg-accent/text-bg-primary never generated CSS (no --color-accent token), so
    // the primary CTA painted no fill and read as plain text. It must use the
    // theme-aware fill utilities instead.
    const cta = screen.getByRole('button', { name: 'Start Session' });
    expect(cta).toHaveClass('bg-cta');
    expect(cta).toHaveClass('text-cta-fg');
    expect(cta).not.toHaveClass('bg-accent');
  });
});
