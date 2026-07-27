import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { OpeningGraph } from './OpeningGraph';
import type { OpeningNode } from '../api';

// This component previously had no tests at all, which is how a graph with zero
// focusable elements, hover-only statistics, one-level-deep auto-collapse and
// colliding d3 join keys shipped. jsdom never lays out, so these assert the
// structure, semantics and interaction model — not pixel geometry.

// jsdom implements no SVG geometry, so `SVGElement.transform` is missing and
// d3-interpolate throws reading `transform.baseVal.consolidate()` when it
// interpolates the transform attribute. Returning null is the documented "no
// current transform" answer, so d3 interpolates from identity. Scoped to this
// file rather than the shared setup: defining the property globally makes other
// libraries' `'transform' in element` feature detection pass and sends them
// down an SVG path jsdom cannot honour.
if (!('transform' in SVGElement.prototype)) {
  Object.defineProperty(SVGElement.prototype, 'transform', {
    configurable: true,
    get: () => ({ baseVal: { consolidate: () => null } }),
  });
}

function node(
  move_san: string,
  games: [w: number, d: number, l: number],
  children?: OpeningNode[]
): OpeningNode {
  const [wins, draws, losses] = games;
  const games_count = wins + draws + losses;
  return {
    move_san,
    ply: 0,
    games_count,
    wins,
    draws,
    losses,
    win_rate: games_count ? Math.round(((wins + 0.5 * draws) / games_count) * 1000) / 10 : 0,
    ...(children ? { children } : {}),
  };
}

/** A deep line, so auto-collapse behaviour is observable. */
function line(moves: string[], games: [number, number, number]): OpeningNode {
  return moves.reduceRight<OpeningNode | undefined>(
    (child, san) => node(san, games, child ? [child] : undefined),
    undefined
  )!;
}

/**
 * Two lines that transpose: `(move_san, depth, parent.move_san)` is identical
 * for `cxd4` and everything under it, while the statistics differ.
 */
const TRANSPOSING_TREE: OpeningNode = node('Start', [10, 0, 18], [
  node('e4', [10, 0, 18], [
    node('c5', [10, 0, 18], [
      node('Nf3', [10, 0, 18], [
        node('Nc6', [7, 0, 0], [node('d4', [7, 0, 0], [node('cxd4', [7, 0, 0])])]),
        node('d6', [3, 0, 18], [node('d4', [3, 0, 18], [node('cxd4', [3, 0, 18])])]),
      ]),
    ]),
  ]),
]);

/** 40+ nodes, so the auto-collapse threshold trips. */
const BIG_TREE: OpeningNode = node(
  'Start',
  [20, 0, 20],
  ['a', 'b', 'c', 'd', 'e', 'f'].map((tag) =>
    line([`${tag}1`, `${tag}2`, `${tag}3`, `${tag}4`, `${tag}5`, `${tag}6`, `${tag}7`], [3, 0, 3])
  )
);

function renderGraph(data: OpeningNode, props: Partial<Parameters<typeof OpeningGraph>[0]> = {}) {
  const onNodeHover = vi.fn();
  const onNodeHoverEnd = vi.fn();
  const onError = vi.fn();
  render(
    <OpeningGraph
      data={data}
      onNodeHover={onNodeHover}
      onNodeHoverEnd={onNodeHoverEnd}
      onError={onError}
      {...props}
    />
  );
  return { onNodeHover, onNodeHoverEnd, onError };
}

const items = () => screen.getAllByRole('treeitem');
const labels = () => items().map((el) => el.getAttribute('aria-label') ?? '');
const byMove = (text: string) =>
  items().find((el) => el.getAttribute('aria-label')?.startsWith(text));

beforeEach(() => vi.clearAllMocks());

describe('OpeningGraph — assistive technology', () => {
  it('exposes the graph as a named tree', () => {
    renderGraph(TRANSPOSING_TREE);

    const tree = screen.getByRole('tree');
    expect(tree).toHaveAccessibleName(/opening repertoire/i);
  });

  it('names every node with its statistics, not colour alone', () => {
    renderGraph(TRANSPOSING_TREE);

    // Score is carried by fill colour in the visual, which is unavailable to
    // screen readers and ambiguous under red/green colour blindness.
    expect(byMove('1. e4')).toHaveAttribute(
      'aria-label',
      '1. e4. 28 games, 10 won, 0 drawn, 18 lost, score 35.7%'
    );
  });

  it('marks expandable nodes with aria-expanded and level', () => {
    renderGraph(TRANSPOSING_TREE);

    const e4 = byMove('1. e4')!;
    expect(e4).toHaveAttribute('aria-expanded', 'true');
    expect(e4).toHaveAttribute('aria-level', '2');
  });

  it('hides the link layer from the tree', () => {
    const { container } = render(
      <OpeningGraph data={TRANSPOSING_TREE} onNodeHover={vi.fn()} onNodeHoverEnd={vi.fn()} />
    );

    expect(container.querySelector('g.links')).toHaveAttribute('aria-hidden', 'true');
  });
});

describe('OpeningGraph — keyboard access', () => {
  it('keeps exactly one tab stop (roving tabindex)', () => {
    renderGraph(TRANSPOSING_TREE);

    expect(items().filter((el) => el.getAttribute('tabindex') === '0')).toHaveLength(1);
  });

  it('moves focus down the visible tree with ArrowDown', () => {
    renderGraph(TRANSPOSING_TREE);

    const start = byMove('Starting position')!;
    start.focus();
    fireEvent.keyDown(start, { key: 'ArrowDown' });

    expect(document.activeElement).toBe(byMove('1. e4'));
  });

  it('collapses with ArrowLeft and re-expands with ArrowRight', async () => {
    renderGraph(TRANSPOSING_TREE);

    const e4 = byMove('1. e4')!;
    e4.focus();
    fireEvent.keyDown(e4, { key: 'ArrowLeft' });

    expect(byMove('1. e4')).toHaveAttribute('aria-expanded', 'false');
    // Collapsed children leave on an exit transition, so the element outlives
    // the state change by one animation.
    await waitFor(() => expect(byMove('1…c5')).toBeUndefined());

    fireEvent.keyDown(byMove('1. e4')!, { key: 'ArrowRight' });
    expect(byMove('1. e4')).toHaveAttribute('aria-expanded', 'true');
    expect(byMove('1…c5')).toBeDefined();
  });

  it('toggles a node with Enter', () => {
    renderGraph(TRANSPOSING_TREE);

    const e4 = byMove('1. e4')!;
    e4.focus();
    fireEvent.keyDown(e4, { key: 'Enter' });

    expect(byMove('1. e4')).toHaveAttribute('aria-expanded', 'false');
  });

  it('surfaces a node’s statistics on focus, not only on hover', () => {
    const { onNodeHover } = renderGraph(TRANSPOSING_TREE);

    fireEvent.focus(byMove('1. e4')!);

    // Touch and keyboard users had no route to these figures at all.
    expect(onNodeHover).toHaveBeenCalledWith(
      expect.objectContaining({ x: expect.any(Number), y: expect.any(Number) }),
      expect.objectContaining({ move_san: 'e4', games_count: 28 })
    );
  });

  it('clears the tooltip on blur', () => {
    const { onNodeHoverEnd } = renderGraph(TRANSPOSING_TREE);

    fireEvent.blur(byMove('1. e4')!);
    expect(onNodeHoverEnd).toHaveBeenCalled();
  });
});

describe('OpeningGraph — data join keys', () => {
  it('renders both halves of a transposition', () => {
    renderGraph(TRANSPOSING_TREE);

    // `cxd4-6-d4` was the same key for both lines, so one element was
    // destroyed and re-created on every update.
    const cxd4 = labels().filter((l) => l.startsWith('3…cxd4'));
    expect(cxd4).toHaveLength(2);
    expect(cxd4).toEqual(
      expect.arrayContaining([
        expect.stringContaining('7 games'),
        expect.stringContaining('21 games'),
      ])
    );
  });

  it('keeps one element per node across a collapse/expand cycle', () => {
    renderGraph(TRANSPOSING_TREE);
    const before = items().length;

    const nf3 = byMove('2. Nf3')!;
    fireEvent.click(nf3);
    fireEvent.click(byMove('2. Nf3')!);

    expect(items()).toHaveLength(before);
    expect(new Set(labels()).size).toBe(before);
  });
});

describe('OpeningGraph — progressive disclosure', () => {
  it('collapses every level of a large tree, not just the first', () => {
    renderGraph(BIG_TREE);

    // Depth 0-3 visible; deeper plies stay hidden until asked for.
    const visible = items().length;
    expect(visible).toBeLessThan(BIG_TREE.children!.length * 8);
    expect(labels().some((l) => l.startsWith('3. a5'))).toBe(false);
  });

  it('reveals one level per click instead of the whole subtree', () => {
    renderGraph(BIG_TREE);

    const before = items().length;
    // `root.each` stopped descending once children were cleared, so only depth
    // 3 was ever collapsed and one click dumped every remaining ply at once.
    fireEvent.click(byMove('2. a3')!);

    expect(items().length).toBe(before + 1);
  });

  it('does not collapse a small tree', () => {
    renderGraph(TRANSPOSING_TREE);

    expect(labels().some((l) => l.startsWith('3…cxd4'))).toBe(true);
  });
});

describe('OpeningGraph — page scrolling', () => {
  it('leaves an unmodified wheel event to the page', () => {
    const { container } = render(
      <OpeningGraph data={TRANSPOSING_TREE} onNodeHover={vi.fn()} onNodeHoverEnd={vi.fn()} />
    );
    const svg = container.querySelector('svg')!;

    const wheel = new WheelEvent('wheel', { deltaY: 120, bubbles: true, cancelable: true });
    svg.dispatchEvent(wheel);

    // The graph used to preventDefault every wheel event, so scrolling toward
    // the legend zoomed the tree instead of moving the page.
    expect(wheel.defaultPrevented).toBe(false);
  });

  it('claims the wheel only when the zoom modifier is held', () => {
    const { container } = render(
      <OpeningGraph data={TRANSPOSING_TREE} onNodeHover={vi.fn()} onNodeHoverEnd={vi.fn()} />
    );
    const svg = container.querySelector('svg')!;

    const wheel = new WheelEvent('wheel', {
      deltaY: 120, ctrlKey: true, bubbles: true, cancelable: true,
    });
    svg.dispatchEvent(wheel);

    expect(wheel.defaultPrevented).toBe(true);
  });
});
