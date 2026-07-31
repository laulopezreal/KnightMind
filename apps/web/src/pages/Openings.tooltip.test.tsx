import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, act } from '@testing-library/react';
import Openings from './Openings';
import type { NodeAnchor } from '../components/OpeningGraph';

// The tooltip was anchored blindly to the hovered node, putting up to 180px of
// it off-screen for any node in the lower right — reachable just by panning.

vi.mock('react-router-dom', () => ({
  useNavigate: () => vi.fn(),
  Link: ({ children, to, ...rest }: { children: React.ReactNode; to: string; [key: string]: unknown }) => <a href={to} {...rest}>{children}</a>,
}));
vi.mock('../context/ChessUsernameContext', () => ({
  useChessUsername: () => ({ username: 'alice' }),
}));

const mockGetOpenings = vi.fn();
// Spread the real module and override only the call under test. A hand-listed
// mock breaks every time the page imports something new from `../api`.
vi.mock('../api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../api')>()),
  getOpenings: (...a: unknown[]) => mockGetOpenings(...a),
}));

// Capture the graph's hover callback so a node anchor can be simulated at any
// viewport position without needing real SVG layout.
let emitHover: ((anchor: NodeAnchor, node: unknown) => void) | null = null;
vi.mock('../components/OpeningGraph', () => ({
  OpeningGraph: ({ onNodeHover }: { onNodeHover: (a: NodeAnchor, n: unknown) => void }) => {
    emitHover = onNodeHover;
    return <div data-testid="opening-graph" />;
  },
}));

const TREE = {
  move_san: 'Start', ply: 0, games_count: 40, wins: 20, draws: 4, losses: 16,
  win_rate: 55,
  children: [
    { move_san: 'e4', ply: 1, games_count: 40, wins: 20, draws: 4, losses: 16, win_rate: 55 },
  ],
  analysis: {
    games_stored: 40, games_seen: 40, games_analyzed: 40, excluded_by_color: 0,
    games_skipped: 0, skipped_unreadable: 0, skipped_not_player: 0, skipped_unfinished: 0,
  min_games: 1,
  },
};

const TOOLTIP_W = 220;
const TOOLTIP_H = 200;

beforeEach(() => {
  vi.clearAllMocks();
  emitHover = null;
  mockGetOpenings.mockResolvedValue(TREE);
  // jsdom reports every box as 0x0; give the tooltip a realistic size so the
  // clamping arithmetic has something to work with.
  vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockReturnValue({
    width: TOOLTIP_W, height: TOOLTIP_H, x: 0, y: 0, top: 0, left: 0,
    right: TOOLTIP_W, bottom: TOOLTIP_H, toJSON: () => ({}),
  } as DOMRect);
  window.innerWidth = 1280;
  window.innerHeight = 800;
});

async function hoverAt(anchor: NodeAnchor) {
  render(<Openings />);
  await screen.findByTestId('opening-graph');
  // The real callback fires from a native d3 listener, outside React's event
  // system, so the resulting state update needs wrapping here.
  await act(async () => { emitHover!(anchor, TREE.children[0]); });
  return waitFor(() => {
    const el = document.querySelector<HTMLElement>('.fixed.z-\\[9999\\]');
    expect(el).not.toBeNull();
    return el!;
  });
}

const position = (el: HTMLElement) => ({
  left: parseFloat(el.style.left),
  top: parseFloat(el.style.top),
});

describe('Openings tooltip placement', () => {
  it('sits beside the node when there is room', async () => {
    const el = await hoverAt({ x: 400, y: 300 });

    expect(position(el).left).toBe(400);
    // Vertically centred on the node.
    expect(position(el).top).toBe(300 - TOOLTIP_H / 2);
  });

  it('stays fully on screen for a node at the bottom-right corner', async () => {
    const el = await hoverAt({ x: 1270, y: 795 });
    const { left, top } = position(el);

    expect(left + TOOLTIP_W).toBeLessThanOrEqual(window.innerWidth);
    expect(top + TOOLTIP_H).toBeLessThanOrEqual(window.innerHeight);
    expect(left).toBeGreaterThanOrEqual(0);
    expect(top).toBeGreaterThanOrEqual(0);
  });

  it('flips to the left of the node rather than overflowing right', async () => {
    const el = await hoverAt({ x: 1200, y: 400 });

    // The exact flipped coordinate, not merely "on screen": the downstream
    // clamp alone satisfies a loose bound, so a clamp-only implementation
    // passed this test while covering the node the user is pointing at.
    expect(position(el).left).toBe(1200 - TOOLTIP_W - 24);
  });

  it('stays on screen for a node above the top edge', async () => {
    const el = await hoverAt({ x: 300, y: 5 });

    expect(position(el).top).toBeGreaterThanOrEqual(0);
  });

  it('is revealed only once positioned, so it never flashes unclamped', async () => {
    const el = await hoverAt({ x: 1270, y: 795 });

    expect(el.style.visibility).toBe('visible');
  });
});
