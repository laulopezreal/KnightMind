import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, act, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import Openings from './Openings';
import type { OpeningNode } from '../api';

// The Opening Explorer used to be a dead end: no outbound link anywhere in the
// page, and a hover-only tooltip as the sole place to read a line's figures.

vi.mock('react-router-dom', () => ({
  useNavigate: () => vi.fn(),
  Link: ({ children, to }: { children: React.ReactNode; to: string }) => (
    <a href={to}>{children}</a>
  ),
}));
vi.mock('../context/ChessUsernameContext', () => ({
  useChessUsername: () => ({ username: 'alice' }),
}));

const mockGetOpenings = vi.fn();
vi.mock('../api', () => ({
  getOpenings: (...a: unknown[]) => mockGetOpenings(...a),
  ApiError: class extends Error {
    statusCode!: number;
    detail?: string;
  },
}));

// Capture the graph's selection callback so a node activation can be simulated.
let emitSelect: ((path: OpeningNode[] | null) => void) | null = null;
vi.mock('../components/OpeningGraph', () => ({
  OpeningGraph: ({ onNodeSelect }: { onNodeSelect?: (p: OpeningNode[] | null) => void }) => {
    emitSelect = onNodeSelect ?? null;
    return <div data-testid="opening-graph" />;
  },
}));

function node(move_san: string, over: Partial<OpeningNode> = {}): OpeningNode {
  return {
    move_san, ply: 0, games_count: 21, wins: 6, draws: 3, losses: 12, win_rate: 35.7,
    ...over,
  };
}

const ANALYSIS = {
  games_stored: 40, games_seen: 40, games_analyzed: 40, excluded_by_color: 0,
  games_skipped: 0, skipped_unreadable: 0, skipped_not_player: 0, skipped_unfinished: 0,
};

// The tree must actually contain the line the tests select. The graph only ever
// reports paths from the tree it rendered, and the page re-resolves a selection
// against the current tree — so a fixture whose tree lacks the selected line
// makes the panel vanish the moment the fetch settles, which is a race, not a
// scenario a user can reach.
const TREE: OpeningNode = {
  ...node('Start', { games_count: 40, wins: 20, draws: 4, losses: 16, win_rate: 55 }),
  children: [
    { ...node('e4'), children: [{ ...node('c5'), children: [node('Nf3')] }] },
  ],
  analysis: ANALYSIS,
};

const SICILIAN = [node('Start'), node('e4'), node('c5'), node('Nf3')];

beforeEach(() => {
  vi.clearAllMocks();
  emitSelect = null;
  localStorage.clear();
  mockGetOpenings.mockResolvedValue(TREE);
});

async function selectLine(path: OpeningNode[]) {
  render(<Openings />);
  await screen.findByTestId('opening-graph');
  await act(async () => { emitSelect!(path); });
}

describe('Openings selection panel', () => {
  it('is absent until a node is activated', async () => {
    render(<Openings />);
    await screen.findByTestId('opening-graph');

    expect(screen.queryByLabelText('Selected line')).not.toBeInTheDocument();
  });

  it('names the selected line in chess notation', async () => {
    await selectLine(SICILIAN);

    expect(screen.getByText('1. e4 c5 2. Nf3')).toBeInTheDocument();
  });

  it('shows the line’s figures without needing a pointer', async () => {
    await selectLine(SICILIAN);

    const panel = screen.getByLabelText('Selected line');
    // Touch and keyboard users had no route to these at all.
    expect(panel).toHaveTextContent('Games');
    expect(panel).toHaveTextContent('21');
    expect(panel).toHaveTextContent('35.7%');
  });

  it('labels the metric score, not win rate', async () => {
    await selectLine(SICILIAN);

    const panel = screen.getByLabelText('Selected line');
    expect(panel).toHaveTextContent('Score');
    expect(panel).not.toHaveTextContent(/win rate/i);
  });

  it('offers a route into the Engine for the selected position', async () => {
    await selectLine(SICILIAN);

    const link = screen.getByRole('link', { name: /analyse in engine/i });
    const href = link.getAttribute('href')!;
    expect(href.startsWith('/engine?fen=')).toBe(true);

    // The FEN must be the position after 1.e4 c5 2.Nf3 — Black to move.
    const fen = decodeURIComponent(href.slice('/engine?fen='.length));
    expect(fen).toContain(' b ');
  });

  it('links to the starting position when the root is selected', async () => {
    await selectLine([node('Start')]);

    expect(screen.getByText('Starting position')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /analyse in engine/i })).toBeInTheDocument();
  });

  it('offers no Engine link when the line cannot be replayed', async () => {
    // A corrupt import must not send a broken FEN on to another page.
    await selectLine([node('Start'), node('e4'), node('Qxf7')]);

    expect(screen.getByLabelText('Selected line')).toBeInTheDocument();
    expect(screen.queryByRole('link', { name: /analyse in engine/i })).not.toBeInTheDocument();
  });

  it('can be dismissed', async () => {
    await selectLine(SICILIAN);
    await userEvent.click(screen.getByRole('button', { name: 'Clear' }));

    expect(screen.queryByLabelText('Selected line')).not.toBeInTheDocument();
  });
});

describe('Openings selection across a data change', () => {
  it('drops a line that the new colour filter does not contain', async () => {
    render(<Openings />);
    await screen.findByTestId('opening-graph');
    await act(async () => { emitSelect!(SICILIAN); });
    expect(screen.getByText('1. e4 c5 2. Nf3')).toBeInTheDocument();

    // "As black" is a different question; the panel must not keep answering
    // the old one with the old numbers.
    mockGetOpenings.mockResolvedValue({
      ...TREE,
      children: [node('d4')],
    });
    await userEvent.selectOptions(
      screen.getByLabelText('Filter openings by color played'), 'black'
    );

    await waitFor(() =>
      expect(screen.queryByLabelText('Selected line')).not.toBeInTheDocument()
    );
  });

  it('refreshes the figures of a line that survives the refetch', async () => {
    render(<Openings />);
    await screen.findByTestId('opening-graph');
    await act(async () => { emitSelect!([node('Start'), node('e4')]); });
    expect(screen.getByLabelText('Selected line')).toHaveTextContent('21');

    // Same line, new numbers after a refetch.
    mockGetOpenings.mockResolvedValue({
      ...TREE,
      children: [node('e4', { games_count: 55, wins: 30, draws: 5, losses: 20, win_rate: 59.1 })],
    });
    await userEvent.click(screen.getByRole('button', { name: 'Refresh' }));

    await waitFor(() =>
      expect(screen.getByLabelText('Selected line')).toHaveTextContent('55')
    );
    expect(screen.getByLabelText('Selected line')).toHaveTextContent('59.1%');
  });
});

describe('Openings failed refresh', () => {
  it('keeps the tree on screen when a refresh fails', async () => {
    render(<Openings />);
    await screen.findByTestId('opening-graph');

    mockGetOpenings.mockRejectedValueOnce(new Error('Network request failed'));
    await userEvent.click(screen.getByRole('button', { name: 'Refresh' }));

    // Discarding the graph would lose the user's zoom and expanded lines to a
    // transient blip — the very thing keeping it mounted was meant to prevent.
    await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent(/couldn’t refresh/i));
    expect(screen.getByTestId('opening-graph')).toBeInTheDocument();
  });

  it('frames a failed refresh politely rather than as a blocking alert', async () => {
    render(<Openings />);
    await screen.findByTestId('opening-graph');

    mockGetOpenings.mockRejectedValueOnce(new Error('Network request failed'));
    await userEvent.click(screen.getByRole('button', { name: 'Refresh' }));

    await waitFor(() => expect(screen.getByRole('status')).toBeInTheDocument());
    // The content below is still readable, so this is status, not alert.
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });

  it('still shows a blocking error when there is no tree to fall back on', async () => {
    mockGetOpenings.mockRejectedValue(new Error('Network request failed'));
    render(<Openings />);

    await waitFor(() => expect(screen.getByRole('alert')).toBeInTheDocument());
    expect(screen.queryByTestId('opening-graph')).not.toBeInTheDocument();
  });
});

describe('Openings refresh', () => {
  it('keeps the graph on screen while refreshing', async () => {
    let resolveRefresh!: (value: unknown) => void;
    render(<Openings />);
    await screen.findByTestId('opening-graph');

    mockGetOpenings.mockImplementationOnce(
      () => new Promise((resolve) => { resolveRefresh = resolve; })
    );
    await userEvent.click(screen.getByRole('button', { name: 'Refresh' }));

    // Replacing the tree with a spinner discarded the user's zoom and every
    // line they had expanded, for a request that usually returns the same data.
    expect(screen.getByTestId('opening-graph')).toBeInTheDocument();
    expect(screen.getByText('Refreshing…')).toBeInTheDocument();

    await act(async () => { resolveRefresh(TREE); });
    expect(screen.getByTestId('opening-graph')).toBeInTheDocument();
  });

  it('keeps the stats footer consistent with the graph while refreshing', async () => {
    render(<Openings />);
    await screen.findByTestId('opening-graph');

    mockGetOpenings.mockImplementationOnce(() => new Promise(() => {}));
    await userEvent.click(screen.getByRole('button', { name: 'Refresh' }));

    // Previously the footer kept rendering while the graph became a spinner,
    // pairing stale numbers with an empty panel.
    expect(screen.getByText('Games Analyzed')).toBeInTheDocument();
    expect(screen.getByTestId('opening-graph')).toBeInTheDocument();
  });
});
