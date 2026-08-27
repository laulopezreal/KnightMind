import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { SessionSummaryCard } from './SessionSummaryCard';

const mockSessionSummary = {
  session_id: 'session-1',
  requested_n: 10,
  pass_count: 8,
  fail_count: 2,
  total_time_ms: 125000, // 2m 5s
  current_streak: 3,
  best_streak: 5,
  hints_used: 1,
  created_at: '2025-01-15T11:58:00Z',
  completed_at: '2025-01-15T12:00:00Z',
  session_type: 'standard' as const,
};

const mockAchievements = [
  { id: '1', name: 'Quick Solver', description: 'Fast completion', icon: '⚡', earned: true },
  { id: '2', name: 'Unearned', description: 'Not yet', icon: '🔒', earned: false },
];

describe('SessionSummaryCard', () => {
  const user = userEvent.setup();

  it('should display session summary heading', () => {
    render(
      <SessionSummaryCard
        sessionSummary={mockSessionSummary}
        achievements={mockAchievements}
        onStartNewSession={vi.fn()}
      />
    );

    // 8/2 = 80% accuracy → the celebratory headline (tone tracks the result,
    // never the old "Successfully Recorded" database receipt).
    expect(screen.getByText('Sharp session!')).toBeInTheDocument();
  });

  it('keeps the headline honest on a rough session', () => {
    render(
      <SessionSummaryCard
        sessionSummary={{ ...mockSessionSummary, pass_count: 2, fail_count: 8 }}
        achievements={mockAchievements}
        onStartNewSession={vi.fn()}
      />
    );

    expect(screen.getByText(/tough one, keep at it/i)).toBeInTheDocument();
  });

  it('should display pass and fail counts', async () => {
    render(
      <SessionSummaryCard
        sessionSummary={mockSessionSummary}
        achievements={mockAchievements}
        onStartNewSession={vi.fn()}
      />
    );

    // findByText: the stats count up to their final value.
    expect(await screen.findByText('8', undefined, { timeout: 4000 })).toBeInTheDocument();
    expect(await screen.findByText('2', undefined, { timeout: 4000 })).toBeInTheDocument();
  });

  it('should display accuracy percentage', async () => {
    render(
      <SessionSummaryCard
        sessionSummary={mockSessionSummary}
        achievements={mockAchievements}
        onStartNewSession={vi.fn()}
      />
    );

    expect(await screen.findByText('80%', undefined, { timeout: 4000 })).toBeInTheDocument();
  });

  it('should display total time', () => {
    render(
      <SessionSummaryCard
        sessionSummary={mockSessionSummary}
        achievements={mockAchievements}
        onStartNewSession={vi.fn()}
      />
    );

    expect(screen.getByText('2m 5s')).toBeInTheDocument();
  });

  it('should display best streak and hints used', async () => {
    render(
      <SessionSummaryCard
        sessionSummary={mockSessionSummary}
        achievements={mockAchievements}
        onStartNewSession={vi.fn()}
      />
    );

    expect(await screen.findByText('5', undefined, { timeout: 4000 })).toBeInTheDocument(); // best streak
    expect(await screen.findByText('1', undefined, { timeout: 4000 })).toBeInTheDocument(); // hints used
  });

  it('should display earned achievements only', () => {
    render(
      <SessionSummaryCard
        sessionSummary={mockSessionSummary}
        achievements={mockAchievements}
        onStartNewSession={vi.fn()}
      />
    );

    expect(screen.getByText('Quick Solver')).toBeInTheDocument();
    expect(screen.queryByText('Unearned')).not.toBeInTheDocument();
  });

  it('should call onStartNewSession when button clicked', async () => {
    const onStartNewSession = vi.fn();
    render(
      <SessionSummaryCard
        sessionSummary={mockSessionSummary}
        achievements={mockAchievements}
        onStartNewSession={onStartNewSession}
      />
    );

    await user.click(screen.getByText('Start New Session'));
    expect(onStartNewSession).toHaveBeenCalledTimes(1);
  });

  it('renders the primary CTA with a solid fill, not the no-op bg-primary', () => {
    render(
      <SessionSummaryCard
        sessionSummary={mockSessionSummary}
        achievements={mockAchievements}
        onStartNewSession={vi.fn()}
      />
    );

    // bg-primary/text-bg-primary generated no CSS (unregistered tokens), so the
    // button read as plain text. It must use the theme-aware fill utilities.
    const cta = screen.getByRole('button', { name: 'Start New Session' });
    expect(cta).toHaveClass('bg-primary');
    expect(cta).toHaveClass('text-bg-primary');
  });

  it('should not show achievements section when none earned', () => {
    const noAchievements = [{ id: '1', name: 'Locked', description: 'Not yet', icon: '🔒', earned: false }];

    render(
      <SessionSummaryCard
        sessionSummary={mockSessionSummary}
        achievements={noAchievements}
        onStartNewSession={vi.fn()}
      />
    );

    expect(screen.queryByText('Achievements Earned')).not.toBeInTheDocument();
  });

  it('shows no missed-puzzle section when session has no failures', () => {
    render(
      <SessionSummaryCard
        sessionSummary={{ ...mockSessionSummary, fail_count: 0, pass_count: 10, missed_puzzles: null }}
        achievements={[]}
        onStartNewSession={vi.fn()}
      />
    );

    expect(screen.queryByText(/missed puzzle/i)).not.toBeInTheDocument();
  });

  it('shows no missed-puzzle section when missed_puzzles is an empty array', () => {
    render(
      <SessionSummaryCard
        sessionSummary={{ ...mockSessionSummary, missed_puzzles: [] }}
        achievements={[]}
        onStartNewSession={vi.fn()}
      />
    );

    expect(screen.queryByText(/missed puzzle/i)).not.toBeInTheDocument();
  });

  it('shows missed puzzles with cause label and review link', () => {
    const summary = {
      ...mockSessionSummary,
      missed_puzzles: [
        {
          puzzle_id: 'p-abc',
          display_name: '12 Mar · Sicilian · move 18',
          cause: 'king_safety_blindness',
          cause_label: 'King safety blindness',
        },
      ],
    };

    render(
      <MemoryRouter>
        <SessionSummaryCard
          sessionSummary={summary}
          achievements={[]}
          onStartNewSession={vi.fn()}
        />
      </MemoryRouter>
    );

    expect(screen.getByText(/missed puzzle/i)).toBeInTheDocument();
    expect(screen.getByText('12 Mar · Sicilian · move 18')).toBeInTheDocument();
    expect(screen.getByText('King safety blindness')).toBeInTheDocument();
    const reviewLink = screen.getByRole('link', { name: /review/i });
    expect(reviewLink).toHaveAttribute('href', '/library/p-abc');
  });

  it('shows honest copy when missed puzzle has no diagnosed cause', () => {
    const summary = {
      ...mockSessionSummary,
      missed_puzzles: [
        {
          puzzle_id: 'p-xyz',
          display_name: '14 Apr · move 22',
          cause: null,
          cause_label: null,
        },
      ],
    };

    render(
      <MemoryRouter>
        <SessionSummaryCard
          sessionSummary={summary}
          achievements={[]}
          onStartNewSession={vi.fn()}
        />
      </MemoryRouter>
    );

    expect(screen.getByText(/cause not yet diagnosed/i)).toBeInTheDocument();
  });

  it('shows plural heading when multiple puzzles were missed', () => {
    const summary = {
      ...mockSessionSummary,
      missed_puzzles: [
        { puzzle_id: 'p-1', display_name: 'move 10', cause: null, cause_label: null },
        { puzzle_id: 'p-2', display_name: 'move 15', cause: null, cause_label: null },
      ],
    };

    render(
      <MemoryRouter>
        <SessionSummaryCard
          sessionSummary={summary}
          achievements={[]}
          onStartNewSession={vi.fn()}
        />
      </MemoryRouter>
    );

    expect(screen.getByText(/missed puzzles \(2\)/i)).toBeInTheDocument();
  });
});
