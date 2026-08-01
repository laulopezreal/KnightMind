import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen, act, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import Openings from './Openings';
import { renderAt } from '../test/router';
import type { OpeningNode } from '../api';

// The whole view lived in localStorage: per-device, invisible, and unshareable.
// Two people could hold the same URL and see different pages, a link to a line
// was impossible to produce, and Back walked out of the page rather than out of
// a selection. These cover the query string as the thing that holds the view.

vi.mock('../context/ChessUsernameContext', () => ({
  useChessUsername: () => ({ username: 'alice' }),
}));

const mockGetOpenings = vi.fn();
vi.mock('../api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../api')>()),
  getOpenings: (...a: unknown[]) => mockGetOpenings(...a),
}));

// The graph is stubbed down to the two things this page talks to it about:
// which line it reports, and which line it is told to open.
let emitSelect: ((path: OpeningNode[] | null) => void) | null = null;
const revealPath = vi.fn(() => true);
vi.mock('../components/OpeningGraph', () => ({
  OpeningGraph: ({
    onNodeSelect,
    graphRef,
  }: {
    onNodeSelect?: (p: OpeningNode[] | null) => void;
    graphRef?: React.RefObject<{ revealPath: (m: string[]) => boolean } | null>;
  }) => {
    emitSelect = onNodeSelect ?? null;
    if (graphRef) {
      graphRef.current = { revealPath } as never;
    }
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
  children: [
    { ...node('e4'), children: [{ ...node('c5', { games_count: 12 }), children: [node('Nf3')] }] },
    node('d4'),
  ],
  analysis: {
    games_stored: 40, games_seen: 40, games_analyzed: 40, excluded_by_color: 0,
    games_skipped: 0, skipped_unreadable: 0, skipped_not_player: 0, skipped_unfinished: 0,
    min_games: 1,
  },
};

const SICILIAN = [node('Start'), node('e4'), node('c5')];

beforeEach(() => {
  vi.clearAllMocks();
  emitSelect = null;
  localStorage.clear();
  mockGetOpenings.mockResolvedValue(TREE);
});

async function renderReady(url = '/openings') {
  const view = renderAt(<Openings />, url);
  await screen.findByTestId('opening-graph');
  return view;
}

/** Query parameters currently in the address bar. */
function params(search: string): URLSearchParams {
  return new URLSearchParams(search);
}

describe('Openings — the URL holds the view', () => {
  it('opens the colour and depth the link names, over the stored preference', async () => {
    localStorage.setItem('knightmind:openings:color_filter', JSON.stringify('white'));
    localStorage.setItem('knightmind:openings:max_ply', JSON.stringify(8));

    await renderReady('/openings?color=black&depth=24');

    // The link is what was shared; the stored value is only a default for a
    // visit that does not name one.
    expect(mockGetOpenings).toHaveBeenCalledWith('alice', 'black', 24);
  });

  it('falls back to the stored preference when the link names none', async () => {
    localStorage.setItem('knightmind:openings:color_filter', JSON.stringify('white'));

    await renderReady();

    expect(mockGetOpenings).toHaveBeenCalledWith('alice', 'white', 12);
  });

  it('ignores a colour or depth the app does not offer', async () => {
    // Hand-edited or truncated URLs must not put the page into a state its own
    // controls cannot represent, nor send junk to the endpoint.
    await renderReady('/openings?color=purple&depth=999');

    expect(mockGetOpenings).toHaveBeenCalledWith('alice', 'both', 12);
  });

  it('writes the view into the URL so copying it shares what you see', async () => {
    const { router } = await renderReady();

    await waitFor(() => {
      expect(params(router.search).get('color')).toBe('both');
      expect(params(router.search).get('depth')).toBe('12');
    });
  });

  it('records a chosen line as moves', async () => {
    const { router } = await renderReady();

    await act(async () => { emitSelect!(SICILIAN); });

    expect(params(router.search).get('line')).toBe('e4_c5');
  });

  it('restores the line a link names, with the current tree’s figures', async () => {
    await renderReady('/openings?line=e4_c5');

    const panel = await screen.findByLabelText('Selected line');
    expect(panel).toHaveTextContent('1. e4 c5');
    // 12, from the tree — not from whatever the link's author was looking at.
    expect(panel).toHaveTextContent('12');
  });

  it('tells the graph to open a line it did not itself choose', async () => {
    await renderReady('/openings?line=e4_c5');

    // Without this the panel describes a line the graph never reveals: the
    // tree auto-collapses, so a shared deep line would be invisible.
    await waitFor(() => expect(revealPath).toHaveBeenCalledWith(['e4', 'c5']));
  });

  it('does not re-open a line the user just clicked', async () => {
    await renderReady();

    await act(async () => { emitSelect!(SICILIAN); });

    // The graph already has it open and focused; re-revealing would refit and
    // drag the view out from under them.
    expect(revealPath).not.toHaveBeenCalled();
  });

  it('drops a line the loaded tree does not contain', async () => {
    const { router } = await renderReady('/openings?line=e4_e5');

    // A link to a Black line opened under the White filter, say. The panel is
    // already gone; the address bar must not keep claiming otherwise.
    await waitFor(() => expect(params(router.search).has('line')).toBe(false));
    expect(screen.queryByLabelText('Selected line')).not.toBeInTheDocument();
  });

  it('keeps the starting position selectable', async () => {
    // The root is a real selection with its own Engine link, and it encodes to
    // an empty line — which must not read as "nothing selected".
    await renderReady('/openings?line=');

    expect(await screen.findByText('Starting position')).toBeInTheDocument();
  });
});

describe('Openings — Back steps out of a selection', () => {
  it('returns to the unselected view', async () => {
    const { router } = await renderReady();

    await act(async () => { emitSelect!(SICILIAN); });
    expect(params(router.search).get('line')).toBe('e4_c5');

    await act(async () => { router.back(); });

    expect(params(router.search).has('line')).toBe(false);
    await waitFor(() =>
      expect(screen.queryByLabelText('Selected line')).not.toBeInTheDocument()
    );
  });

  it('steps back through lines one at a time', async () => {
    const { router } = await renderReady();

    await act(async () => { emitSelect!([node('Start'), node('e4')]); });
    await act(async () => { emitSelect!(SICILIAN); });

    await act(async () => { router.back(); });

    expect(params(router.search).get('line')).toBe('e4');
  });

  it('does not stack a second entry for the line already selected', async () => {
    const { router } = await renderReady();

    await act(async () => { emitSelect!(SICILIAN); });
    await act(async () => { emitSelect!(SICILIAN); });

    // One Back must be enough; a duplicate entry makes the control feel stuck.
    await act(async () => { router.back(); });

    expect(params(router.search).has('line')).toBe(false);
  });

  it('does not make filter changes something to walk back through', async () => {
    const user = userEvent.setup();
    const { router } = await renderReady();

    await user.selectOptions(screen.getByLabelText('Filter openings by color played'), 'white');
    await waitFor(() => expect(params(router.search).get('color')).toBe('white'));
    await user.selectOptions(screen.getByLabelText('Tree depth in moves'), '24');
    await waitFor(() => expect(params(router.search).get('depth')).toBe('24'));

    await act(async () => { router.back(); });

    // Filters replace rather than push: Back leaves the page, it does not undo
    // six presses of fiddling.
    expect(params(router.search).get('color')).toBe('white');
    expect(params(router.search).get('depth')).toBe('24');
  });
});
