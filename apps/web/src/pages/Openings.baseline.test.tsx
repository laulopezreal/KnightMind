import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen, act, waitFor } from '@testing-library/react';
import Openings from './Openings';
import { renderAt } from '../test/router';
import type { OpeningNode, OpeningBaseline } from '../api';

// A line's own score says how the user did; it cannot say whether that was
// good. These cover the half that comes from outside — including every way it
// can be absent, because a baseline that quietly renders as 0% or as a missing
// row is worse than one that is honestly not there.

vi.mock('../context/ChessUsernameContext', () => ({
  useChessUsername: () => ({ username: 'alice' }),
}));

const mockGetOpenings = vi.fn();
const mockGetBaseline = vi.fn();
vi.mock('../api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../api')>()),
  getOpenings: (...a: unknown[]) => mockGetOpenings(...a),
  getBaseline: (...a: unknown[]) => mockGetBaseline(...a),
}));

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
    eco: null, opening_name: null,
    ...over,
  };
}

const TREE: OpeningNode = {
  ...node('Start', { games_count: 40, wins: 20, draws: 4, losses: 16, win_rate: 55 }),
  children: [{ ...node('e4'), children: [node('c5')] }],
  analysis: {
    games_stored: 40, games_seen: 40, games_analyzed: 40, excluded_by_color: 0, excluded_by_date: 0, since_days: null,
    games_skipped: 0, skipped_unreadable: 0, skipped_not_player: 0, skipped_unfinished: 0,
    min_games: 1,
  },
};

const SICILIAN = [node('Start'), node('e4'), node('c5')];

function baseline(over: Partial<OpeningBaseline> = {}): OpeningBaseline {
  return {
    games: 120000,
    expected_score: 52.5,
    band: { low: 1600, high: 1800, label: '1600–1800' },
    source: 'lichess',
    ...over,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  emitSelect = null;
  localStorage.clear();
  mockGetOpenings.mockResolvedValue(TREE);
  mockGetBaseline.mockResolvedValue(baseline());
});

/** Render already filtered to a colour, since "both" has no baseline to show. */
async function selectAs(color: 'white' | 'black' = 'white') {
  const view = renderAt(<Openings />, `/openings?color=${color}`);
  await screen.findByTestId('opening-graph');
  await act(async () => { emitSelect!(SICILIAN); });
  return view;
}

const panel = () => screen.getByLabelText('Selected line');

describe('Openings — comparison against players at the same rating', () => {
  it('states the gap, the expectation and the band it came from', async () => {
    await selectAs();

    // 35.7 against 52.5 — without the second number the first one is just a
    // fact about the user, not a reason to do anything.
    await waitFor(() => expect(panel()).toHaveTextContent('-16.8 vs 52.5% expected'));
    expect(panel()).toHaveTextContent('1600–1800');
  });

  it('asks about the position the line reaches, for the colour on screen', async () => {
    await selectAs('black');

    await waitFor(() => expect(mockGetBaseline).toHaveBeenCalled());
    const [username, fen, color] = mockGetBaseline.mock.calls[0];
    expect(username).toBe('alice');
    expect(color).toBe('black');
    // 1. e4 c5 — a real position, not the starting one.
    expect(fen).toContain('rnbqkbnr/pp1ppppp');
  });

  it('says which control produces a comparison when the filter is “both”', async () => {
    renderAt(<Openings />, '/openings?color=both');
    await screen.findByTestId('opening-graph');
    await act(async () => { emitSelect!(SICILIAN); });

    // Not an error: under "both" the user's own figure already mixes games
    // from either side, so there is genuinely nothing to compare it against.
    expect(panel()).toHaveTextContent('Filter by colour to compare with your rating');
    expect(mockGetBaseline).not.toHaveBeenCalled();
  });

  it('says the position is too rare rather than implying a verdict', async () => {
    mockGetBaseline.mockResolvedValue(baseline({ expected_score: null, games: 12 }));

    await selectAs();

    // Omitting the row silently would let the reader assume the comparison
    // was withheld for a reason that flatters them.
    await waitFor(() => expect(panel()).toHaveTextContent('Too rare to compare (12 games)'));
  });

  it('names all ratings when it does not know the user’s', async () => {
    mockGetBaseline.mockResolvedValue(baseline({ band: null }));

    await selectAs();

    // Must not imply a peer group the user may not be in.
    await waitFor(() => expect(panel()).toHaveTextContent('(all ratings)'));
  });

  it('stays quiet when the lookup fails', async () => {
    mockGetBaseline.mockRejectedValue(new Error('explorer down'));

    await selectAs();

    // The page is already rendered and useful; a comparison that could not be
    // fetched is a missing extra, not an error worth interrupting anyone over.
    await waitFor(() => expect(panel()).toHaveTextContent('35.7%'));
    expect(panel()).not.toHaveTextContent('expected');
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });

  it('never shows one line’s comparison against another line’s score', async () => {
    // The figures sit side by side in one row, so a stale baseline reads as a
    // verdict on the new line.
    mockGetBaseline.mockResolvedValue(baseline());
    await selectAs();
    await waitFor(() => expect(panel()).toHaveTextContent('expected'));

    let release: (value: OpeningBaseline) => void = () => {};
    mockGetBaseline.mockReturnValue(new Promise<OpeningBaseline>(r => { release = r; }));
    await act(async () => { emitSelect!([node('Start'), node('e4')]); });

    expect(panel()).not.toHaveTextContent('expected');

    await act(async () => { release(baseline({ expected_score: 49.0 })); });
    await waitFor(() => expect(panel()).toHaveTextContent('49% expected'));
  });

  it('cancels a lookup the user has already moved on from', async () => {
    // Not just cosmetic: an abandoned lookup still spends the caller's share
    // of the endpoint's per-principal rate limit, and this route fires on
    // every line selected.
    const signals: (AbortSignal | undefined)[] = [];
    mockGetBaseline.mockImplementation((...args: unknown[]) => {
      signals.push((args[3] as { signal?: AbortSignal } | undefined)?.signal);
      return new Promise<OpeningBaseline>(() => {});
    });

    await selectAs();
    await act(async () => { emitSelect!([node('Start'), node('e4')]); });

    expect(signals).toHaveLength(2);
    expect(signals[0]?.aborted).toBe(true);
    expect(signals[1]?.aborted).toBe(false);
  });

  it('drops the comparison when the selection is cleared', async () => {
    await selectAs();
    await waitFor(() => expect(panel()).toHaveTextContent('expected'));

    await act(async () => { emitSelect!(null); });

    expect(screen.queryByLabelText('Selected line')).not.toBeInTheDocument();
  });
});
