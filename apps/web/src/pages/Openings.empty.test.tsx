import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import Openings from './Openings';
// Resolves to the mocked module below. The page branches on `instanceof
// ApiError`, so the error thrown here must be the very class the page imports.
import { ApiError } from '../api';

// The API answers a 200 with a root node even when nothing matched, so an
// "empty" tree is a truthy object. These cover the states that produced fully
// chromed panels wrapped around no data.

let mockUsername = 'alice';
const mockNavigate = vi.fn();

vi.mock('react-router-dom', () => ({
  useNavigate: () => mockNavigate,
  Link: ({ children, to, ...rest }: { children: React.ReactNode; to: string; [key: string]: unknown }) => <a href={to} {...rest}>{children}</a>,
}));
vi.mock('../context/ChessUsernameContext', () => ({
  useChessUsername: () => ({ username: mockUsername }),
}));

const mockGetOpenings = vi.fn();
// Spread the real module and override only the call under test. A hand-listed
// mock breaks every time the page imports something new from `../api`.
vi.mock('../api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../api')>()),
  getOpenings: (...a: unknown[]) => mockGetOpenings(...a),
}));

vi.mock('../components/OpeningGraph', () => ({
  OpeningGraph: () => <div data-testid="opening-graph" />,
}));

/** A root node with zero games — what the API returns when nothing matched. */
function emptyTree(analysis: Record<string, number>) {
  return {
    move_san: 'Start', ply: 0, games_count: 0, wins: 0, draws: 0, losses: 0,
    win_rate: 0, children: [],
    analysis: {
      games_stored: 0, games_seen: 0, games_analyzed: 0, excluded_by_color: 0,
      games_skipped: 0, skipped_unreadable: 0, skipped_not_player: 0,
      skipped_unfinished: 0, ...analysis,
    },
  };
}

const POPULATED_TREE = {
  move_san: 'Start', ply: 0, games_count: 40, wins: 20, draws: 4, losses: 16,
  win_rate: 55, children: [
    { move_san: 'e4', ply: 1, games_count: 40, wins: 20, draws: 4, losses: 16, win_rate: 55 },
  ],
  analysis: {
    games_stored: 40, games_seen: 40, games_analyzed: 40, excluded_by_color: 0,
    games_skipped: 0, skipped_unreadable: 0, skipped_not_player: 0, skipped_unfinished: 0,
  },
};

beforeEach(() => {
  vi.clearAllMocks();
  mockUsername = 'alice';
  localStorage.clear();
});

describe('Openings — nothing imported yet (first run)', () => {
  it('offers an import route instead of an error with a dead Retry', async () => {
    mockGetOpenings.mockRejectedValue(new ApiError('No games found', 404));
    render(<Openings />);

    expect(await screen.findByText('No games imported yet')).toBeInTheDocument();
    // A 404 here means "nothing imported", which is not a failure.
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    expect(
      screen.queryByRole('button', { name: /retry loading openings/i })
    ).not.toBeInTheDocument();
  });

  it('routes to the import screen from the empty state', async () => {
    mockGetOpenings.mockRejectedValue(new ApiError('No games found', 404));
    render(<Openings />);

    await userEvent.click(await screen.findByRole('button', { name: 'Import games' }));
    expect(mockNavigate).toHaveBeenCalledWith('/');
  });

  it('hides the colour filter when there is nothing to filter', async () => {
    mockGetOpenings.mockRejectedValue(new ApiError('No games found', 404));
    render(<Openings />);

    await screen.findByText('No games imported yet');
    expect(
      screen.queryByLabelText('Filter openings by color played')
    ).not.toBeInTheDocument();
  });

  it('still reports a real server error as an error', async () => {
    mockGetOpenings.mockRejectedValue(new ApiError('Internal Server Error', 500));
    render(<Openings />);

    expect(await screen.findByRole('alert')).toHaveTextContent('Internal Server Error');
    expect(screen.queryByText('No games imported yet')).not.toBeInTheDocument();
  });
});

describe('Openings — a 200 with an empty tree', () => {
  it('does not render graph chrome around an empty tree', async () => {
    mockGetOpenings.mockResolvedValue(emptyTree({ games_stored: 12, excluded_by_color: 12 }));
    localStorage.setItem('knightmind:openings:color_filter', JSON.stringify('black'));
    render(<Openings />);

    await waitFor(() => expect(screen.queryByTestId('opening-graph')).not.toBeInTheDocument());
    // No legend, no stats, no zoom controls explaining a graph that isn't there.
    expect(screen.queryByText('Score:')).not.toBeInTheDocument();
    expect(screen.queryByText('Games Analyzed')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Zoom in' })).not.toBeInTheDocument();
  });

  it('names the colour filter as the reason and offers a way out', async () => {
    mockGetOpenings.mockResolvedValue(emptyTree({ games_stored: 12, excluded_by_color: 12 }));
    localStorage.setItem('knightmind:openings:color_filter', JSON.stringify('black'));
    render(<Openings />);

    expect(await screen.findByText('No games as Black yet')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Show all games' }));

    await waitFor(() =>
      expect(mockGetOpenings).toHaveBeenLastCalledWith('alice', 'both', 12)
    );
  });

  it('does not claim the subtitle covers all imported games when it is empty', async () => {
    mockGetOpenings.mockResolvedValue(emptyTree({ games_stored: 12, excluded_by_color: 12 }));
    localStorage.setItem('knightmind:openings:color_filter', JSON.stringify('black'));
    render(<Openings />);

    await screen.findByText('No games as Black yet');
    expect(screen.getByText('No games as Black to chart yet.')).toBeInTheDocument();
  });

  it('blames unreadable games when the colour filter is not the cause', async () => {
    mockGetOpenings.mockResolvedValue(
      emptyTree({ games_stored: 9, games_seen: 9, games_skipped: 9, skipped_unreadable: 9 })
    );
    render(<Openings />);

    expect(await screen.findByText('None of your games could be analysed')).toBeInTheDocument();
    expect(screen.getByText(/9 of 9 stored games/)).toBeInTheDocument();
  });
});

describe('Openings — partially analysed archive', () => {
  it('warns when stored games did not reach the tree', async () => {
    mockGetOpenings.mockResolvedValue({
      ...POPULATED_TREE,
      analysis: {
        ...POPULATED_TREE.analysis,
        games_stored: 50, games_seen: 50, games_analyzed: 40,
        games_skipped: 10, skipped_unfinished: 10,
      },
    });
    render(<Openings />);

    expect(await screen.findByTestId('opening-graph')).toBeInTheDocument();
    expect(screen.getByText(/10 of 50 stored games could not be analysed/)).toBeInTheDocument();
  });

  it('stays quiet when every stored game was analysed', async () => {
    mockGetOpenings.mockResolvedValue(POPULATED_TREE);
    render(<Openings />);

    expect(await screen.findByTestId('opening-graph')).toBeInTheDocument();
    expect(screen.queryByText(/could not be analysed/)).not.toBeInTheDocument();
  });

  it('does not warn about games the colour filter excluded on purpose', async () => {
    mockGetOpenings.mockResolvedValue({
      ...POPULATED_TREE,
      analysis: {
        ...POPULATED_TREE.analysis,
        games_stored: 90, games_seen: 90, games_analyzed: 40,
        excluded_by_color: 50, games_skipped: 0,
      },
    });
    render(<Openings />);

    expect(await screen.findByTestId('opening-graph')).toBeInTheDocument();
    expect(screen.queryByText(/could not be analysed/)).not.toBeInTheDocument();
  });
});
