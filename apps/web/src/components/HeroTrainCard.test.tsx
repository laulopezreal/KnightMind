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

  it('frames the due count as optional availability beside a five-puzzle standard session', () => {
    render(<HeroTrainCard {...defaultProps} />);

    expect(screen.getByText('5')).toBeInTheDocument();
    expect(screen.getByText('puzzles available for review')).toBeInTheDocument();
    expect(screen.getByText(/standard session is 5 puzzles/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Start Standard Session (5 puzzles)' })).toBeInTheDocument();
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

  it('surfaces the 4-hour horizon when caught up and more are coming', () => {
    // due_in_4h is fetched already; the caught-up screen should use it instead
    // of a dead-end "check back later".
    render(<HeroTrainCard {...defaultProps} dueCount={0} dueIn4h={3} />);

    expect(screen.getByText(/3 more puzzles will be ready within 4 hours/i)).toBeInTheDocument();
  });

  it('falls back to next-review time when caught up with none due in 4h', () => {
    render(<HeroTrainCard {...defaultProps} dueCount={0} dueIn4h={0} nextReviewAt="2025-01-15T14:00:00Z" />);

    expect(screen.getByText(/Your next review is/i)).toBeInTheDocument();
  });

  it('keeps the Next review caption in the warmup state (body does not state it)', () => {
    // Regression: the caption-suppression guard must not fire in warmup — the
    // warmup body copy never mentions the review time, so hiding the caption
    // would drop the information entirely.
    render(
      <HeroTrainCard
        {...defaultProps}
        needsWarmup={true}
        daysSinceLastSession={8}
        dueCount={0}
        dueIn4h={0}
        nextReviewAt="2025-01-15T14:00:00Z"
      />
    );

    expect(screen.getByText('Welcome Back')).toBeInTheDocument();
    expect(screen.getByText(/^Next review:/)).toBeInTheDocument();
  });

  it('suppresses the duplicate Next review caption only in the caught-up state', () => {
    render(<HeroTrainCard {...defaultProps} dueCount={0} dueIn4h={0} nextReviewAt="2025-01-15T14:00:00Z" />);

    // Body states it once ("Your next review is …"); the caption must not repeat it.
    expect(screen.getByText(/Your next review is/i)).toBeInTheDocument();
    expect(screen.queryByText(/^Next review:/)).not.toBeInTheDocument();
  });

  it('does not duplicate next-review guidance after completing the caught-up day', () => {
    render(
      <HeroTrainCard
        {...defaultProps}
        completedToday={true}
        dueCount={0}
        dueIn4h={0}
        nextReviewAt="2025-01-15T14:00:00Z"
      />
    );

    expect(screen.getByText(/Your next review is in 2h/i)).toBeInTheDocument();
    expect(screen.queryByText(/^Next review:/)).not.toBeInTheDocument();
  });

  it('does not claim "0 puzzles waiting" for a first-timer with nothing generated', () => {
    render(<HeroTrainCard {...defaultProps} totalSessions={0} dueCount={0} />);

    expect(screen.getByText('Ready to Start Training?')).toBeInTheDocument();
    expect(screen.getByText(/Import your Chess.com games/i)).toBeInTheDocument();
    expect(screen.queryByText(/0 puzzles waiting/i)).not.toBeInTheDocument();
  });

  it('asks a first-timer with nothing generated to import, and drops the empty counter', () => {
    // The copy already says "import your games"; the CTA used to say "Start
    // First Session" and route to the Train page, whose only action was "Go to
    // Home". Label and destination now come from one predicate (utils/trainEntry).
    render(<HeroTrainCard {...defaultProps} totalSessions={0} dueCount={0} />);

    expect(screen.getByRole('button', { name: 'Import Your Games' })).toBeInTheDocument();
    expect(screen.queryByText('Start First Session')).not.toBeInTheDocument();
    // No "0 / puzzles due" counter when there is no queue to report.
    expect(screen.queryByText(/puzzles due/i)).not.toBeInTheDocument();
  });

  it('still offers a first session to a user who has puzzles but has never trained', () => {
    render(<HeroTrainCard {...defaultProps} totalSessions={0} dueCount={7} />);

    expect(screen.getByRole('button', { name: 'Start First Session' })).toBeInTheDocument();
    expect(screen.getByText('7')).toBeInTheDocument();
  });

  it('should call onStartSession when button is clicked', async () => {
    const onStartSession = vi.fn();
    render(<HeroTrainCard {...defaultProps} onStartSession={onStartSession} />);

    await user.click(screen.getByText('Start Standard Session (5 puzzles)'));
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

    expect(screen.getByText('puzzle available for review')).toBeInTheDocument();
  });

  it('should have proper aria-labelledby', () => {
    render(<HeroTrainCard {...defaultProps} />);

    const section = screen.getByRole('region', { name: /train today/i });
    expect(section).toBeInTheDocument();
  });

  it('shows completed-today hero with due puzzles remaining', () => {
    render(<HeroTrainCard {...defaultProps} completedToday={true} dueCount={5} />);

    expect(screen.getByText("Today's training is complete")).toBeInTheDocument();
    expect(screen.getByText(/You completed a training session today/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Train more (5 puzzles)' })).toBeInTheDocument();
    // Due count still visible (as availability, not obligation)
    expect(screen.getByText('5')).toBeInTheDocument();
  });

  it('shows completed-today hero with no due puzzles (prefixed caught-up)', () => {
    render(<HeroTrainCard {...defaultProps} completedToday={true} dueCount={0} />);

    expect(screen.getByText("Today's training is complete")).toBeInTheDocument();
    const body = screen.getByText(/Today's training is complete\./i);
    expect(body).toBeInTheDocument();
    // Secondary optional button is Browse Puzzles since due=0
    expect(screen.getByRole('button', { name: 'Browse Puzzles' })).toBeInTheDocument();
  });

  it('does not enter completed-today state for a first-time user', () => {
    // A first-timer who somehow sent completedToday=true should still onboard normally.
    render(<HeroTrainCard {...defaultProps} completedToday={true} totalSessions={0} dueCount={7} />);

    expect(screen.queryByText("Today's training is complete")).not.toBeInTheDocument();
    expect(screen.getByText('Ready to Start Training?')).toBeInTheDocument();
  });

  it('does not enter completed-today state for a warmup user', () => {
    render(
      <HeroTrainCard
        {...defaultProps}
        completedToday={true}
        needsWarmup={true}
        daysSinceLastSession={10}
      />
    );

    expect(screen.queryByText("Today's training is complete")).not.toBeInTheDocument();
    expect(screen.getByText('Welcome Back')).toBeInTheDocument();
  });

  it('pre-completion states remain unchanged when completedToday is false', () => {
    render(<HeroTrainCard {...defaultProps} completedToday={false} dueCount={3} />);

    expect(screen.getByText('Train Today')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Start Standard Session (5 puzzles)' })).toBeInTheDocument();
  });

  it('gives the daily default a 44px minimum target', () => {
    render(<HeroTrainCard {...defaultProps} />);
    expect(screen.getByRole('button', { name: 'Start Standard Session (5 puzzles)' })).toHaveClass('min-h-11');
  });

  it('renders the CTA with a solid fill via the registered primary token', () => {
    render(<HeroTrainCard {...defaultProps} />);

    // bg-accent never generated CSS (no --color-accent token), so the CTA read as
    // plain text. It now uses the registered bg-primary/text-bg-primary fill.
    const cta = screen.getByRole('button', { name: 'Start Standard Session (5 puzzles)' });
    expect(cta).toHaveClass('bg-primary');
    expect(cta).toHaveClass('text-bg-primary');
    expect(cta).not.toHaveClass('bg-accent');
  });
});
