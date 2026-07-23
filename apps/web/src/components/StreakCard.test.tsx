import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { StreakCard } from './StreakCard';

vi.mock('../utils/time', () => ({
  formatRelativeTime: (iso: string | null) => iso ? '2h ago' : 'N/A',
}));

describe('StreakCard', () => {
  it('should render Consistency heading', () => {
    render(<StreakCard streakDays={5} lastSessionAt={null} />);

    expect(screen.getByText('Consistency')).toBeInTheDocument();
  });

  it('should display streak count', () => {
    render(<StreakCard streakDays={7} lastSessionAt={null} />);

    expect(screen.getByText('7')).toBeInTheDocument();
    expect(screen.getByText('day streak')).toBeInTheDocument();
  });

  it('should show "Keep going" when streak is active', () => {
    render(<StreakCard streakDays={3} lastSessionAt={null} />);

    expect(screen.getByText('Keep going')).toBeInTheDocument();
  });

  it('should show "Resume your streak" when streak is 0 but has past sessions', () => {
    render(<StreakCard streakDays={0} lastSessionAt="2025-01-14T12:00:00Z" />);

    expect(screen.getByText('Resume your streak')).toBeInTheDocument();
  });

  it('should show "Start your streak today" for new users', () => {
    render(<StreakCard streakDays={0} lastSessionAt={null} />);

    expect(screen.getByText('Start your streak today')).toBeInTheDocument();
  });

  it('should show last session time when available', () => {
    render(<StreakCard streakDays={3} lastSessionAt="2025-01-15T10:00:00Z" />);

    expect(screen.getByText(/Last session:/)).toBeInTheDocument();
  });

  it('should not show last session when null', () => {
    render(<StreakCard streakDays={3} lastSessionAt={null} />);

    expect(screen.queryByText(/Last session:/)).not.toBeInTheDocument();
  });

  it('should have proper aria-labelledby', () => {
    render(<StreakCard streakDays={5} lastSessionAt={null} />);

    const section = screen.getByRole('region', { name: /consistency/i });
    expect(section).toBeInTheDocument();
  });

  it('renders the rhythm strip as a single labelled image', () => {
    render(<StreakCard streakDays={3} lastSessionAt={null} />);

    // One img with an aggregate label instead of seven separately-announced cells.
    expect(screen.getByRole('img', { name: /current streak: 3 days/i })).toBeInTheDocument();
  });

  it('labels the rhythm strip as inactive when there is no streak', () => {
    render(<StreakCard streakDays={0} lastSessionAt={null} />);

    expect(screen.getByRole('img', { name: /no active streak/i })).toBeInTheDocument();
  });

  it('notes truncation when the streak exceeds the strip width', () => {
    render(<StreakCard streakDays={12} lastSessionAt={null} />);

    expect(screen.getByRole('img', { name: /current streak: 12 days \(showing the last 7\)/i })).toBeInTheDocument();
  });
});
