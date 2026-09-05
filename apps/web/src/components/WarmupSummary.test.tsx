import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { WarmupSummary } from './WarmupSummary';

vi.mock('react-router-dom', async (importOriginal) => {
  const actual = await importOriginal<typeof import('react-router-dom')>();
  return {
    ...actual,
    Link: ({ children, to, ...props }: { children: React.ReactNode; to: string; [key: string]: unknown }) => (
      <a href={String(to)} {...props} onClick={(event) => event.preventDefault()}>{children}</a>
    ),
  };
});

const mockSessionSummary = {
  session_id: 'warmup-1',
  requested_n: 5,
  pass_count: 4,
  fail_count: 1,
  total_time_ms: 60000,
  current_streak: 4,
  best_streak: 4,
  hints_used: 0,
  created_at: '2025-01-15T11:59:00Z',
  completed_at: '2025-01-15T12:00:00Z',
};

describe('WarmupSummary', () => {
  const user = userEvent.setup();

  it('should display warmup complete heading', () => {
    render(<WarmupSummary sessionSummary={mockSessionSummary} onContinue={vi.fn()} />);

    expect(screen.getByText(/Warmup complete/i)).toBeInTheDocument();
  });

  it('should display accuracy percentage', () => {
    render(<WarmupSummary sessionSummary={mockSessionSummary} onContinue={vi.fn()} />);

    // 4 pass, 1 fail = 80%
    expect(screen.getByText('80%')).toBeInTheDocument();
  });

  it('should display pass and fail counts', () => {
    render(<WarmupSummary sessionSummary={mockSessionSummary} onContinue={vi.fn()} />);

    expect(screen.getByText('4')).toBeInTheDocument();
    expect(screen.getByText('1')).toBeInTheDocument();
  });

  it('should show high retention feedback for >= 80%', () => {
    render(<WarmupSummary sessionSummary={mockSessionSummary} onContinue={vi.fn()} />);

    expect(screen.getByText(/Great retention/)).toBeInTheDocument();
  });

  it('should show moderate feedback for 60-79%', () => {
    const summary = { ...mockSessionSummary, pass_count: 3, fail_count: 2 };
    render(<WarmupSummary sessionSummary={summary} onContinue={vi.fn()} />);

    expect(screen.getByText(/Some patterns need brushing up/)).toBeInTheDocument();
  });

  it('should show low retention feedback for < 60%', () => {
    const summary = { ...mockSessionSummary, pass_count: 1, fail_count: 4 };
    render(<WarmupSummary sessionSummary={summary} onContinue={vi.fn()} />);

    expect(screen.getByText(/Time to rebuild/)).toBeInTheDocument();
  });

  it('renders Back to Dashboard as the sole primary closeout and calls onContinue once', async () => {
    const onContinue = vi.fn();
    render(<WarmupSummary sessionSummary={mockSessionSummary} onContinue={onContinue} />);

    const closeout = screen.getByRole('button', { name: 'Back to Dashboard' });
    expect(closeout).toHaveClass('bg-primary', 'text-bg-primary');
    expect(screen.queryByText('Continue to Dashboard')).not.toBeInTheDocument();
    expect(screen.getAllByRole('button')).toEqual([closeout]);
    await user.click(closeout);
    expect(onContinue).toHaveBeenCalledTimes(1);
  });

  it('should have accessible region', () => {
    render(<WarmupSummary sessionSummary={mockSessionSummary} onContinue={vi.fn()} />);

    const section = screen.getByRole('region', { name: /warmup/i });
    expect(section).toBeInTheDocument();
  });

  it('renders one missed puzzle with a truthful review link and optional cause', async () => {
    const onContinue = vi.fn();
    render(<WarmupSummary sessionSummary={{
      ...mockSessionSummary,
      missed_puzzles: [{
        puzzle_id: 'p-abc',
        display_name: '12 Mar · Sicilian · move 18',
        cause: 'king_safety_blindness',
        cause_label: 'King safety blindness',
      }],
    }} onContinue={onContinue} />);

    expect(screen.getByRole('heading', { name: 'Missed puzzle' })).toBeInTheDocument();
    expect(screen.getByText('King safety blindness')).toBeInTheDocument();
    const reviewLink = screen.getByRole('link', { name: 'Review 12 Mar · Sicilian · move 18' });
    expect(reviewLink).toHaveAttribute('href', '/library/p-abc?from=session');
    expect(reviewLink).toHaveClass('min-h-11', 'min-w-11', 'inline-flex', 'km-focus-visible');
    await user.click(reviewLink);
    expect(onContinue).not.toHaveBeenCalled();
  });

  it('renders multiple missed puzzle names without inventing missing cause text', () => {
    render(<WarmupSummary sessionSummary={{
      ...mockSessionSummary,
      missed_puzzles: [
        { puzzle_id: 'p-1', display_name: 'First learning moment', cause: null, cause_label: null },
        { puzzle_id: 'p-2', display_name: 'Second learning moment', cause: 'calculation', cause_label: 'Calculation depth' },
      ],
    }} onContinue={vi.fn()} />);

    expect(screen.getByRole('heading', { name: 'Missed puzzles (2)' })).toBeInTheDocument();
    expect(screen.getByText('First learning moment')).toBeInTheDocument();
    expect(screen.getByText('Second learning moment')).toBeInTheDocument();
    expect(screen.getByText('Calculation depth')).toBeInTheDocument();
    expect(screen.getAllByRole('link')).toHaveLength(2);
  });

  it.each([
    ['absent', undefined],
    ['empty', []],
  ])('does not render missed-puzzle learning when data is %s', (_label, missedPuzzles) => {
    render(<WarmupSummary
      sessionSummary={{ ...mockSessionSummary, missed_puzzles: missedPuzzles }}
      onContinue={vi.fn()}
    />);

    expect(screen.queryByRole('heading', { name: /missed puzzle/i })).not.toBeInTheDocument();
    expect(screen.queryByRole('link')).not.toBeInTheDocument();
  });

  it('keeps long missed-puzzle identity and cause text wrapping safely', () => {
    const longName = 'Championship preparation game · Sicilian Najdorf poisoned pawn · move 38';
    const longCause = 'Missed the long forcing sequence after overlooking the opponent’s back-rank threat';
    render(<WarmupSummary sessionSummary={{
      ...mockSessionSummary,
      missed_puzzles: [{ puzzle_id: 'p-long', display_name: longName, cause: 'calculation', cause_label: longCause }],
    }} onContinue={vi.fn()} />);

    expect(screen.getByText(longName)).toHaveClass('whitespace-normal', 'break-words');
    expect(screen.getByText(longCause)).toHaveClass('whitespace-normal', 'break-words');
    expect(screen.getByText(longName).parentElement).toHaveClass('min-w-0', 'flex-1');
  });
});
