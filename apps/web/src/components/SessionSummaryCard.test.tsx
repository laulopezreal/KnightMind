import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { SessionSummaryCard } from './SessionSummaryCard';

// SessionSummaryCard renders a <Link> (Back to Dashboard). Mock Link as a plain
// anchor so tests that do not need routing can render without MemoryRouter.
// Tests that explicitly assert href/routing behaviour use MemoryRouter directly.
vi.mock('react-router-dom', async (importOriginal) => {
  const actual = await importOriginal<typeof import('react-router-dom')>();
  return {
    ...actual,
    Link: ({ children, to, ...props }: { children: React.ReactNode; to: string; [key: string]: unknown }) => (
      <a href={String(to)} {...props}>{children}</a>
    ),
  };
});

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

  it('distinguishes review attempts from the number of puzzles in the session', async () => {
    render(
      <SessionSummaryCard
        sessionSummary={{
          ...mockSessionSummary,
          requested_n: 5,
          pass_count: 2,
          fail_count: 13,
        }}
        achievements={mockAchievements}
        onStartNewSession={vi.fn()}
      />
    );

    const result = screen.getByLabelText('Session result');
    expect(result).toHaveTextContent('Passed attempts');
    expect(result).toHaveTextContent('Failed attempts');
    expect(result).toHaveTextContent('Attempt accuracy');
    expect(await screen.findByText('2', undefined, { timeout: 4000 })).toBeInTheDocument();
    expect(await screen.findByText('13', undefined, { timeout: 4000 })).toBeInTheDocument();
    expect(await screen.findByText('13%', undefined, { timeout: 4000 })).toBeInTheDocument();
    const context = screen.getByText('15 review attempts across 5 puzzles.');
    expect(context).toBeVisible();
    expect(context).toHaveAttribute('id', 'session-attempt-context');
    expect(result).toHaveAttribute('aria-describedby', 'session-attempt-context');
    expect(result).toHaveAccessibleDescription('15 review attempts across 5 puzzles.');
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

  it('renders Back to Dashboard as the primary link with solid fill', () => {
    render(
      <SessionSummaryCard
        sessionSummary={mockSessionSummary}
        achievements={mockAchievements}
        onStartNewSession={vi.fn()}
      />
    );

    // Back to Dashboard is the primary closeout: solid bg-primary fill.
    const link = screen.getByRole('link', { name: 'Back to Dashboard' });
    expect(link).toHaveClass('bg-primary');
    expect(link).toHaveClass('text-bg-primary');
    expect(link).toHaveAttribute('href', '/dashboard');
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

    expect(screen.queryByText('Achievements earned')).not.toBeInTheDocument();
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
    const reviewLink = screen.getByRole('link', { name: 'Review 12 Mar · Sicilian · move 18' });
    expect(reviewLink).toHaveAttribute('href', '/library/p-abc?from=session');
  });

  it('hands a diagnosed miss off to Insights after the learning context', () => {
    const summary = {
      ...mockSessionSummary,
      missed_puzzles: [
        {
          puzzle_id: 'p-pattern',
          display_name: 'Critical moment',
          cause: '  king_safety_blindness  ',
          cause_label: '  King safety blindness  ',
        },
        {
          puzzle_id: 'p-undiagnosed',
          display_name: 'Later miss',
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

    const patternLinks = screen.getAllByRole('link', { name: 'Review your patterns' });
    expect(patternLinks).toHaveLength(1);
    expect(patternLinks[0]).toHaveAttribute('href', '/insights');
    expect(patternLinks[0]).toHaveClass('inline-flex', 'min-h-11', 'km-focus-visible');

    const missedList = screen.getByRole('list', { name: 'Missed puzzles' });
    const detailsHeading = screen.getByRole('heading', { name: 'Session details' });
    expect(missedList.compareDocumentPosition(patternLinks[0]) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(patternLinks[0].compareDocumentPosition(detailsHeading) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();

    const dashboardLink = screen.getByRole('link', { name: 'Back to Dashboard' });
    expect(dashboardLink).toHaveClass('bg-primary', 'text-bg-primary');
    expect(patternLinks[0]).not.toHaveClass('bg-primary', 'text-bg-primary');
  });

  it.each([
    ['missing cause', null, 'Diagnosed label'],
    ['empty cause', '', 'Diagnosed label'],
    ['whitespace-only cause', '   ', 'Diagnosed label'],
    ['missing cause label', 'calculation', null],
    ['empty cause label', 'calculation', ''],
    ['whitespace-only cause label', 'calculation', '   '],
  ])('does not offer pattern review for a miss with %s', (_case, cause, causeLabel) => {
    render(
      <MemoryRouter>
        <SessionSummaryCard
          sessionSummary={{
            ...mockSessionSummary,
            missed_puzzles: [
              { puzzle_id: 'p-no-pattern', display_name: 'Critical moment', cause, cause_label: causeLabel },
            ],
          }}
          achievements={[]}
          onStartNewSession={vi.fn()}
        />
      </MemoryRouter>
    );

    expect(screen.queryByRole('link', { name: 'Review your patterns' })).not.toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Review Critical moment' })).toHaveAttribute(
      'href',
      '/library/p-no-pattern?from=session',
    );
  });

  it('does not offer pattern review for an empty session', () => {
    render(
      <MemoryRouter>
        <SessionSummaryCard
          sessionSummary={{
            ...mockSessionSummary,
            pass_count: 0,
            fail_count: 0,
            missed_puzzles: [
              {
                puzzle_id: 'p-stale',
                display_name: 'Stale miss',
                cause: 'calculation',
                cause_label: 'Calculation',
              },
            ],
          }}
          achievements={[]}
          onStartNewSession={vi.fn()}
        />
      </MemoryRouter>
    );

    expect(screen.queryByRole('link', { name: 'Review your patterns' })).not.toBeInTheDocument();
  });

  it('puts missed-puzzle learning before supporting session details', () => {
    const summary = {
      ...mockSessionSummary,
      missed_puzzles: [
        { puzzle_id: 'p-order', display_name: 'Critical moment', cause: null, cause_label: null },
      ],
    };

    render(
      <MemoryRouter>
        <SessionSummaryCard
          sessionSummary={summary}
          achievements={mockAchievements}
          onStartNewSession={vi.fn()}
        />
      </MemoryRouter>
    );

    const missedHeading = screen.getByRole('heading', { name: 'Missed puzzle' });
    const detailsHeading = screen.getByRole('heading', { name: 'Session details' });
    expect(missedHeading.compareDocumentPosition(detailsHeading) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it('places the closeout actions before five full-detail missed puzzles', async () => {
    const onStartNewSession = vi.fn();
    const missedPuzzles = Array.from({ length: 5 }, (_, index) => ({
      puzzle_id: `p-five-${index + 1}`,
      display_name: `Championship preparation game ${index + 1} · Sicilian Najdorf poisoned pawn · move ${38 + index}`,
      cause: 'calculation',
      cause_label: 'Missed the long forcing sequence after overlooking the opponent’s back-rank threat',
    }));

    render(
      <MemoryRouter>
        <SessionSummaryCard
          sessionSummary={{
            ...mockSessionSummary,
            pass_count: 5,
            fail_count: 5,
            missed_puzzles: missedPuzzles,
          }}
          achievements={mockAchievements}
          onStartNewSession={onStartNewSession}
        />
      </MemoryRouter>
    );

    const result = screen.getByLabelText('Session result');
    const closeout = screen.getByRole('group', { name: 'Session closeout actions' });
    const missedHeading = screen.getByRole('heading', { name: 'Missed puzzles (5)' });
    const detailsHeading = screen.getByRole('heading', { name: 'Session details' });
    expect(result.compareDocumentPosition(closeout) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(closeout.compareDocumentPosition(missedHeading) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(closeout.compareDocumentPosition(detailsHeading) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();

    const reviewLinks = missedPuzzles.map(({ display_name }) => (
      screen.getByRole('link', { name: `Review ${display_name}` })
    ));
    expect(reviewLinks).toHaveLength(5);
    expect(screen.getByRole('link', { name: 'Review your patterns' })).toBeInTheDocument();

    const dashboardLink = screen.getByRole('link', { name: 'Back to Dashboard' });
    expect(dashboardLink).toHaveAttribute('href', '/dashboard');
    expect(dashboardLink).toHaveClass('bg-primary', 'text-bg-primary');

    const startButton = screen.getByRole('button', { name: 'Start New Session' });
    expect(startButton).toHaveClass('border');
    expect(startButton).not.toHaveClass('bg-primary');
    await user.click(startButton);
    expect(onStartNewSession).toHaveBeenCalledTimes(1);
  });

  it('keeps long puzzle identity and cause text wrapping instead of truncating', () => {
    const longName = 'Championship preparation game · Sicilian Najdorf poisoned pawn · move 38';
    const longCause = 'Missed the long forcing sequence after overlooking the opponent’s back-rank threat';
    const summary = {
      ...mockSessionSummary,
      missed_puzzles: [
        { puzzle_id: 'p-long', display_name: longName, cause: 'calculation', cause_label: longCause },
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

    const identity = screen.getByText(longName);
    const cause = screen.getByText(longCause);
    expect(identity).toHaveClass('whitespace-normal', 'break-words');
    expect(identity).not.toHaveClass('truncate');
    expect(cause).toHaveClass('whitespace-normal', 'break-words');
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

  // --- YELLOW-1 regression: Review hit-target contract ---
  it('Review link carries framework 44px sizing utilities for WCAG 2.5.5 touch target', () => {
    const summary = {
      ...mockSessionSummary,
      missed_puzzles: [
        {
          puzzle_id: 'p-abc',
          display_name: 'Test puzzle',
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

    const reviewLink = screen.getByRole('link', { name: /review test puzzle/i });
    // Class guards the 44×44 contract — if removed the touch target regresses.
    expect(reviewLink).toHaveClass('min-h-11');
    expect(reviewLink).toHaveClass('min-w-11');
    // Flex layout is what makes the min-h/w apply as the interactive area.
    expect(reviewLink).toHaveClass('inline-flex');
    expect(reviewLink).toHaveClass('items-center');
    expect(reviewLink).toHaveClass('justify-center');
  });

  // --- YELLOW-2 regression: Review URL carries ?from=session ---
  it('Review link href includes ?from=session for session-origin back-navigation', () => {
    const summary = {
      ...mockSessionSummary,
      missed_puzzles: [
        {
          puzzle_id: 'p-xyz',
          display_name: 'Another puzzle',
          cause: 'some_cause',
          cause_label: 'Some cause',
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

    const reviewLink = screen.getByRole('link', { name: /review another puzzle/i });
    expect(reviewLink).toHaveAttribute('href', '/library/p-xyz?from=session');
  });

  // --- Daily ritual closeout: Back to Dashboard primary, Start New Session secondary ---

  it('renders Back to Dashboard as primary closeout link', () => {
    render(
      <MemoryRouter>
        <SessionSummaryCard
          sessionSummary={mockSessionSummary}
          achievements={[]}
          onStartNewSession={vi.fn()}
        />
      </MemoryRouter>
    );

    const link = screen.getByRole('link', { name: 'Back to Dashboard' });
    expect(link).toBeInTheDocument();
    expect(link).toHaveAttribute('href', '/dashboard');
    // Primary style: solid bg-primary fill
    expect(link).toHaveClass('bg-primary');
    expect(link).toHaveClass('text-bg-primary');
  });

  it('renders Start New Session as quieter secondary action', () => {
    render(
      <MemoryRouter>
        <SessionSummaryCard
          sessionSummary={mockSessionSummary}
          achievements={[]}
          onStartNewSession={vi.fn()}
        />
      </MemoryRouter>
    );

    const btn = screen.getByRole('button', { name: 'Start New Session' });
    expect(btn).toBeInTheDocument();
    // Secondary style: outline border, not the solid fill
    expect(btn).toHaveClass('border');
    expect(btn).not.toHaveClass('bg-primary');
  });

  it('calls onStartNewSession when Start New Session is clicked', async () => {
    const user = userEvent.setup();
    const onStartNewSession = vi.fn();

    render(
      <MemoryRouter>
        <SessionSummaryCard
          sessionSummary={mockSessionSummary}
          achievements={[]}
          onStartNewSession={onStartNewSession}
        />
      </MemoryRouter>
    );

    await user.click(screen.getByRole('button', { name: 'Start New Session' }));
    expect(onStartNewSession).toHaveBeenCalledTimes(1);
  });

  it('does not show a false completion message for a session with 0 puzzles', () => {
    render(
      <MemoryRouter>
        <SessionSummaryCard
          sessionSummary={{ ...mockSessionSummary, pass_count: 0, fail_count: 0 }}
          achievements={[]}
          onStartNewSession={vi.fn()}
        />
      </MemoryRouter>
    );

    // The generic "Session complete" headline is honest; no false celebration.
    expect(screen.getByText('Session complete')).toBeInTheDocument();
    expect(screen.queryByText(/all due puzzles are complete/i)).not.toBeInTheDocument();
  });
});
