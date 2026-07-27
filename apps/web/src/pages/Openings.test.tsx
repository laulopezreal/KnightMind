import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import Openings from './Openings';

let mockUsername = 'testplayer';
const mockNavigate = vi.fn();

vi.mock('react-router-dom', () => ({
  useNavigate: () => mockNavigate,
  Link: ({ children, to, ...rest }: { children: React.ReactNode; to: string; [key: string]: unknown }) => <a href={to} {...rest}>{children}</a>,
}));

vi.mock('../context/ChessUsernameContext', () => ({
  useChessUsername: () => ({ username: mockUsername }),
}));

const mockGetOpenings = vi.fn();

vi.mock('../api', () => ({
  getOpenings: (...args: unknown[]) => mockGetOpenings(...args),
  ApiError: class extends Error { detail?: string },
}));

// Isolate the page's legend/state logic from the graph renderer.
vi.mock('../components/OpeningGraph', () => ({
  OpeningGraph: () => <div data-testid="opening-graph" />,
}));

const MOCK_TREE = {
  move_san: 'start', ply: 0, games_count: 42, wins: 20, draws: 12, losses: 10,
  win_rate: 0.48, children: [],
};

// Mock d3 minimally — renderTree uses d3 heavily
vi.mock('d3', () => ({
  select: () => ({
    selectAll: () => ({ remove: vi.fn() }),
    attr: function () { return this; },
    append: function () { return this; },
  }),
  hierarchy: () => ({ descendants: () => [], links: () => [] }),
  tree: () => ({
    size: function () { return this; },
    separation: function () { return this; },
  }),
  linkHorizontal: () => ({
    x: function () { return this; },
    y: function () { return this; },
  }),
}));

describe('Openings', () => {
  beforeEach(() => {
    vi.resetAllMocks();
    mockUsername = 'testplayer';
    mockGetOpenings.mockRejectedValue(new Error('Not loaded'));
  });

  it('should render page heading', () => {
    mockGetOpenings.mockReturnValue(new Promise(() => {}));
    render(<Openings />);

    expect(screen.getByText('Opening Explorer')).toBeInTheDocument();
  });

  it('should redirect when no username', () => {
    mockUsername = '';
    render(<Openings />);

    expect(mockNavigate).toHaveBeenCalledWith('/');
  });

  it('should show color filter options', () => {
    mockGetOpenings.mockReturnValue(new Promise(() => {}));
    render(<Openings />);

    expect(screen.getByDisplayValue('All games')).toBeInTheDocument();
  });

  it('should show Analyzing button while loading', () => {
    mockGetOpenings.mockReturnValue(new Promise(() => {}));
    render(<Openings />);

    // Auto-fetch triggers on mount, so button shows "Analyzing..."
    expect(screen.getByText('Analyzing...')).toBeInTheDocument();
  });

  it('should show loading state while fetching', () => {
    mockGetOpenings.mockReturnValue(new Promise(() => {}));
    render(<Openings />);

    expect(screen.getByRole('status')).toBeInTheDocument();
  });

  it('should show error when fetch fails', async () => {
    mockGetOpenings.mockRejectedValue(new Error('Network error'));
    render(<Openings />);

    await waitFor(() => {
      expect(screen.getByText('Network error')).toBeInTheDocument();
    });
  });

  it('should show score legend once a tree is loaded', async () => {
    mockGetOpenings.mockResolvedValue(MOCK_TREE);
    render(<Openings />);

    expect(await screen.findByText('Score:')).toBeInTheDocument();
  });

  it('labels the metric as score, never as win rate', async () => {
    mockGetOpenings.mockResolvedValue(MOCK_TREE);
    render(<Openings />);

    // (wins + half draws) / games is a score, not a win rate: a line drawn
    // every time scores 50% while winning none of them.
    await screen.findByText('Score:');
    expect(screen.getByText(/wins \+ ½ draws/i)).toBeInTheDocument();
    expect(screen.queryByText('Win Rate:')).not.toBeInTheDocument();
  });

  it('should not show the score legend on error (no graph to explain)', async () => {
    mockGetOpenings.mockRejectedValue(new Error('Network error'));
    render(<Openings />);

    // Error card + Retry appear; the graph legend must not, since there is no
    // graph rendered beneath it.
    expect(await screen.findByText('Network error')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /retry loading openings/i })).toBeInTheDocument();
    expect(screen.queryByText('Score:')).not.toBeInTheDocument();
  });

  it('counts tree nodes as move sequences, not unique positions', async () => {
    // Two transposing lines reach the same position through two distinct nodes,
    // so the node count cannot be reported as "unique positions".
    mockGetOpenings.mockResolvedValue({
      ...MOCK_TREE,
      children: [
        { move_san: 'e4', ply: 1, games_count: 1, wins: 1, draws: 0, losses: 0, win_rate: 100 },
        { move_san: 'd4', ply: 1, games_count: 1, wins: 1, draws: 0, losses: 0, win_rate: 100 },
      ],
    });
    render(<Openings />);

    expect(await screen.findByText('Move Sequences')).toBeInTheDocument();
    // Root ("Start") is excluded — it is not a move.
    expect(screen.getByText('2')).toBeInTheDocument();
    expect(screen.queryByText('Unique Positions')).not.toBeInTheDocument();
  });
});
