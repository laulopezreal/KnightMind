import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import Openings from './Openings';
import { renderAt } from '../test/router';
import type { OpeningNode } from '../api';

// Every game ever played was pooled into one tree, so a line fixed in April
// still read as a weakness with two years of losses in it sitting beside last
// week's wins. These cover the window control, the sentence that has to name
// it, and the empty state that must not send someone with a full archive to
// the import screen because they took a month off.

vi.mock('../context/ChessUsernameContext', () => ({
  useChessUsername: () => ({ username: 'alice' }),
}));

const mockGetOpenings = vi.fn();
vi.mock('../api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../api')>()),
  getOpenings: (...a: unknown[]) => mockGetOpenings(...a),
  getBaseline: vi.fn().mockRejectedValue(new Error('not under test')),
}));

vi.mock('../components/OpeningGraph', () => ({
  OpeningGraph: () => <div data-testid="opening-graph" />,
}));

function node(move_san: string, over: Partial<OpeningNode> = {}): OpeningNode {
  return {
    move_san, ply: 0, games_count: 21, wins: 6, draws: 3, losses: 12, win_rate: 35.7,
    eco: null, opening_name: null,
    ...over,
  };
}

function analysis(over: Record<string, unknown> = {}) {
  return {
    games_stored: 40, games_seen: 40, games_analyzed: 40, excluded_by_color: 0,
    excluded_by_date: 0, since_days: null, games_skipped: 0, skipped_unreadable: 0,
    skipped_not_player: 0, skipped_unfinished: 0, min_games: 1,
    ...over,
  };
}

const TREE: OpeningNode = {
  ...node('Start', { games_count: 40, win_rate: 55 }),
  children: [{ ...node('e4'), children: [node('c5')] }],
  analysis: analysis(),
};

/** A 200 whose tree is empty because the window excluded everything. */
const NOTHING_LATELY: OpeningNode = {
  ...node('Start', { games_count: 0, wins: 0, draws: 0, losses: 0, win_rate: 0 }),
  children: [],
  analysis: analysis({
    games_stored: 400, games_seen: 0, games_analyzed: 0,
    excluded_by_date: 400, since_days: 30,
  }),
};

beforeEach(() => {
  vi.clearAllMocks();
  localStorage.clear();
  mockGetOpenings.mockResolvedValue(TREE);
});

const periodControl = () => screen.getByLabelText('Time period covered');

/** The page's own sentence about what is on screen.
 *  Scoped deliberately: the window control's own options read "Last 90 days"
 *  too, so an unscoped text query passes whatever the subtitle says. */
const subtitle = () =>
  within(screen.getByRole('heading', { name: /Opening Explorer/i }).closest('section')!);

/** Wait for the page, not the graph: an empty window renders no graph at all,
 *  which is exactly the case several of these cover. */
async function ready(url = '/openings') {
  const view = renderAt(<Openings />, url);
  await screen.findByRole('heading', { name: /Opening Explorer/i });
  await waitFor(() => expect(mockGetOpenings).toHaveBeenCalled());
  return view;
}

describe('Openings — recency window', () => {
  it('covers the whole archive until asked otherwise', async () => {
    await ready();

    // All-time is the honest default: a repertoire you are still building is
    // best read over everything.
    expect(mockGetOpenings).toHaveBeenCalledWith('alice', 'both', 12, null);
    expect(await screen.findByLabelText('Time period covered')).toHaveValue('all');
  });

  it('narrows the request to the window chosen', async () => {
    const user = userEvent.setup();
    await ready();

    await user.selectOptions(periodControl(), '90');

    await waitFor(() =>
      expect(mockGetOpenings).toHaveBeenLastCalledWith('alice', 'both', 12, 90)
    );
  });

  it('carries the window in the URL', async () => {
    const user = userEvent.setup();
    const { router } = await ready();

    await user.selectOptions(periodControl(), '365');

    await waitFor(() =>
      expect(new URLSearchParams(router.search).get('period')).toBe('365')
    );
  });

  it('opens the window a link names', async () => {
    await ready('/openings?period=30');

    expect(mockGetOpenings).toHaveBeenCalledWith('alice', 'both', 12, 30);
    expect(await screen.findByLabelText('Time period covered')).toHaveValue('30');
  });

  it('treats “all time” in a link as a choice, not a missing one', async () => {
    // The distinction the three-valued parse exists for: `all` is null, and
    // null must not fall through to whatever this device last used.
    localStorage.setItem('knightmind:openings:period', JSON.stringify(90));

    await ready('/openings?period=all');

    expect(mockGetOpenings).toHaveBeenCalledWith('alice', 'both', 12, null);
  });

  it('falls back to the stored window when the link names none', async () => {
    localStorage.setItem('knightmind:openings:period', JSON.stringify(90));

    await ready();

    expect(mockGetOpenings).toHaveBeenCalledWith('alice', 'both', 12, 90);
  });

  it('ignores a window the app does not offer', async () => {
    await ready('/openings?period=7');

    expect(mockGetOpenings).toHaveBeenCalledWith('alice', 'both', 12, null);
  });

  it('remembers the window for next time', async () => {
    const user = userEvent.setup();
    await ready();

    await user.selectOptions(periodControl(), '90');

    await waitFor(() =>
      expect(localStorage.getItem('knightmind:openings:period')).toBe('90')
    );
  });
});

describe('Openings — saying which window is on screen', () => {
  it('names the window in the subtitle', async () => {
    await ready('/openings?period=90');

    // A tree over 90 days and a tree over five years look identical; reading
    // the wrong one as the whole picture is what this filter exists to stop.
    await waitFor(() =>
      expect(subtitle().getByText(/last 90 days/i)).toBeInTheDocument()
    );
  });

  it('says nothing extra when the window is all time', async () => {
    await ready('/openings?period=all');

    expect(subtitle().queryByText(/last \d+ days/i)).not.toBeInTheDocument();
  });
});

describe('Openings — nothing played lately', () => {
  beforeEach(() => {
    mockGetOpenings.mockResolvedValue(NOTHING_LATELY);
  });

  it('does not tell someone with a full archive to import games', async () => {
    await ready('/openings?period=30');

    // The failure this guards: 400 imported games, a month off, and the page
    // sends them to the import screen.
    expect(screen.queryByText(/Import your Chess.com games/i)).not.toBeInTheDocument();
    expect(await screen.findByText(/No games last 30 days/i)).toBeInTheDocument();
  });

  it('says how many games are outside the window', async () => {
    await ready('/openings?period=30');

    expect(await screen.findByText(/400 imported games/i)).toBeInTheDocument();
  });

  it('offers to widen the window rather than a dead retry', async () => {
    const user = userEvent.setup();
    await ready('/openings?period=30');

    await user.click(await screen.findByRole('button', { name: /Show all time/i }));

    await waitFor(() =>
      expect(mockGetOpenings).toHaveBeenLastCalledWith('alice', 'both', 12, null)
    );
  });

  it('still blames the colour filter when that is the real cause', async () => {
    // The window branch must not swallow the case it does not explain.
    mockGetOpenings.mockResolvedValue({
      ...node('Start', { games_count: 0, wins: 0, draws: 0, losses: 0, win_rate: 0 }),
      children: [],
      analysis: analysis({ games_seen: 40, excluded_by_color: 40, excluded_by_date: 0 }),
    });

    await ready('/openings?color=white');

    expect(await screen.findByText(/No games as White yet/i)).toBeInTheDocument();
  });
});
