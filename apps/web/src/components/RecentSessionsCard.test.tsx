import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { RecentSessionsCard } from './RecentSessionsCard';

const mockSessions = [
  {
    session_id: 's1',
    requested_n: 10,
    pass_count: 8,
    fail_count: 2,
    total_time_ms: 120000,
    current_streak: 3,
    best_streak: 5,
    hints_used: 0,
    created_at: '2025-01-15T12:00:00Z',
    completed_at: '2025-01-15T12:02:00Z',
  },
  {
    session_id: 's2',
    requested_n: 10,
    pass_count: 6,
    fail_count: 4,
    total_time_ms: 180000,
    current_streak: 1,
    best_streak: 3,
    hints_used: 1,
    created_at: '2025-01-14T12:00:00Z',
    completed_at: '2025-01-14T12:03:00Z',
  },
];

describe('RecentSessionsCard', () => {
  const user = userEvent.setup();

  it('should not render when no sessions', () => {
    const { container } = render(<RecentSessionsCard sessions={[]} />);

    expect(container.firstChild).toBeNull();
  });

  it('should render heading', () => {
    render(<RecentSessionsCard sessions={mockSessions} />);

    expect(screen.getByText('Recent sessions')).toBeInTheDocument();
  });

  it('should display session data', () => {
    render(<RecentSessionsCard sessions={mockSessions} />);

    // First session: 8P 2F 80%
    expect(screen.getByText('8P')).toBeInTheDocument();
    expect(screen.getByText('2F')).toBeInTheDocument();
    expect(screen.getByText('80%')).toBeInTheDocument();
  });

  it('should show streak indicator', () => {
    render(<RecentSessionsCard sessions={mockSessions} />);

    // Best streak 5 for first session
    expect(screen.getByText('🔥5')).toBeInTheDocument();
  });

  it('names each row in full, without ARIA on the abbreviation spans', () => {
    // axe flagged aria-prohibited-attr on these spans: ARIA forbids aria-label
    // on a generic element and AT support for it is unreliable, so "4 passed"
    // was never dependable. The row's own accessible name carries the sentence.
    render(<RecentSessionsCard sessions={mockSessions} />);

    const [firstRow] = screen.getAllByRole('listitem');
    expect(firstRow).toHaveAccessibleName(/8 passed, 2 failed, 80% accuracy/);
    expect(firstRow).toHaveAccessibleName(/best streak 5/);
    expect(firstRow.querySelectorAll('span[aria-label]')).toHaveLength(0);
  });

  it('should have list role', () => {
    render(<RecentSessionsCard sessions={mockSessions} />);

    expect(screen.getByRole('list')).toBeInTheDocument();
  });

  it('should show collapse/expand button when collapsible', () => {
    render(<RecentSessionsCard sessions={mockSessions} collapsible={true} />);

    expect(screen.getByText('Collapse')).toBeInTheDocument();
  });

  it('should toggle collapse on button click', async () => {
    render(<RecentSessionsCard sessions={mockSessions} collapsible={true} />);

    const button = screen.getByText('Collapse');
    await user.click(button);

    expect(screen.getByText('Expand')).toBeInTheDocument();
  });

  it('should not show collapse button by default', () => {
    render(<RecentSessionsCard sessions={mockSessions} />);

    expect(screen.queryByText('Collapse')).not.toBeInTheDocument();
    expect(screen.queryByText('Expand')).not.toBeInTheDocument();
  });

  it('should start collapsed when defaultExpanded is false', () => {
    render(<RecentSessionsCard sessions={mockSessions} collapsible={true} defaultExpanded={false} />);

    expect(screen.getByText('Expand')).toBeInTheDocument();
  });
});
