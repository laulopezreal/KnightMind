import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { RecentlyTrickyCard } from './RecentlyTrickyCard';

vi.mock('react-router-dom', () => ({
  Link: ({ children, to, ...props }: { children: React.ReactNode; to: string; [key: string]: unknown }) => (
    <a href={to} {...props}>{children}</a>
  ),
}));

vi.mock('../utils/time', () => ({
  formatRelativeTime: () => '2h ago',
}));

const mockPuzzles = [
  { puzzle_id: 'p1', title: 'Fork Attack', display_name: 'Fork Attack', fail_count: 3, last_attempted_at: '2025-01-15T10:00:00Z' },
  { puzzle_id: 'p2', title: 'Pin Defense', display_name: 'Pin Defense', fail_count: 2, last_attempted_at: '2025-01-15T09:00:00Z' },
];

describe('RecentlyTrickyCard', () => {
  it('should not render when no puzzles', () => {
    const { container } = render(<RecentlyTrickyCard puzzles={[]} totalCount={0} />);

    expect(container.firstChild).toBeNull();
  });

  it('should render heading', () => {
    render(<RecentlyTrickyCard puzzles={mockPuzzles} totalCount={2} />);

    expect(screen.getByText('Recently tricky')).toBeInTheDocument();
  });

  it('should display puzzle titles', () => {
    render(<RecentlyTrickyCard puzzles={mockPuzzles} totalCount={2} />);

    expect(screen.getByText('Fork Attack')).toBeInTheDocument();
    expect(screen.getByText('Pin Defense')).toBeInTheDocument();
  });

  it('should display fail counts', () => {
    render(<RecentlyTrickyCard puzzles={mockPuzzles} totalCount={2} />);

    expect(screen.getByText(/Failed 3×/)).toBeInTheDocument();
    expect(screen.getByText(/Failed 2×/)).toBeInTheDocument();
  });

  it('should show remaining count when totalCount exceeds puzzles', () => {
    render(<RecentlyTrickyCard puzzles={mockPuzzles} totalCount={5} />);

    expect(screen.getByText(/and 3 more tricky puzzles/)).toBeInTheDocument();
  });

  it('should not show remaining count when all shown', () => {
    render(<RecentlyTrickyCard puzzles={mockPuzzles} totalCount={2} />);

    expect(screen.queryByText(/and.*more/)).not.toBeInTheDocument();
  });

  it('should link each puzzle to its own library detail view', () => {
    render(<RecentlyTrickyCard puzzles={mockPuzzles} totalCount={2} />);

    const links = screen.getAllByRole('link');
    // Link by puzzle_id to the specific puzzle so the user can re-attempt the
    // exact position they struggled with — NOT by title-as-motif, which filtered
    // by a motif that doesn't exist and produced an empty, mislabeled session.
    expect(links[0]).toHaveAttribute('href', '/library/p1');
    expect(links[1]).toHaveAttribute('href', '/library/p2');
  });
});

describe('RecentlyTrickyCard — display_name (design §8, rollout step 2)', () => {
  it('renders provenance when the puzzle has no nickname yet', () => {
    // The bug this card had: it filters fail_count >= 2 and the nickname is
    // written asynchronously, so `title` is null for every gap in that race --
    // and a rejected naming call leaves it null permanently. The card showed a
    // column of "Untitled Puzzle" on the surface dedicated to naming puzzles.
    const unnamed = [
      {
        puzzle_id: 'p3',
        title: null as unknown as string,
        display_name: '12 Mar · Sicilian · move 18',
        fail_count: 4,
        last_attempted_at: '2025-01-15T10:00:00Z',
      },
    ];

    render(<RecentlyTrickyCard puzzles={unnamed} totalCount={1} />);

    expect(screen.getByText('12 Mar · Sicilian · move 18')).toBeInTheDocument();
    expect(screen.queryByText('Untitled Puzzle')).not.toBeInTheDocument();
  });

  it('still prefers the nickname when there is one', () => {
    render(<RecentlyTrickyCard puzzles={mockPuzzles} totalCount={2} />);

    expect(screen.getByText('Fork Attack')).toBeInTheDocument();
  });
});
