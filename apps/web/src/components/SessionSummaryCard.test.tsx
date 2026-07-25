import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
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

  it('should display pass and fail counts', () => {
    render(
      <SessionSummaryCard
        sessionSummary={mockSessionSummary}
        achievements={mockAchievements}
        onStartNewSession={vi.fn()}
      />
    );

    expect(screen.getByText('8')).toBeInTheDocument();
    expect(screen.getByText('2')).toBeInTheDocument();
  });

  it('should display accuracy percentage', () => {
    render(
      <SessionSummaryCard
        sessionSummary={mockSessionSummary}
        achievements={mockAchievements}
        onStartNewSession={vi.fn()}
      />
    );

    expect(screen.getByText('80%')).toBeInTheDocument();
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

  it('should display best streak and hints used', () => {
    render(
      <SessionSummaryCard
        sessionSummary={mockSessionSummary}
        achievements={mockAchievements}
        onStartNewSession={vi.fn()}
      />
    );

    expect(screen.getByText('5')).toBeInTheDocument(); // best streak
    expect(screen.getByText('1')).toBeInTheDocument(); // hints used
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
});
