import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, waitFor, fireEvent } from '@testing-library/react';
import { createRef } from 'react';
import { OpeningGraph, type OpeningGraphHandle } from './OpeningGraph';
import type { OpeningNode } from '../api';

// The zoom/fit/resize subsystem had no test at all, and each piece of it has
// already shipped a bug: "Fit to view" was a zoom *reset* that left an
// over-shrunk graph exactly as illegible; the ResizeObserver's immediate
// callback refitted straight over the transform just restored, silently undoing
// zoom preservation on every refresh; and a resize could yank away a view the
// user had chosen by hand.
//
// jsdom cannot lay out, so `fit()` returns early on a zero-size box and
// `d3.pointer` has nothing to work from. Both are given a real size below; the
// assertions are then about which transform was applied and when, never about
// pixels.

if (!('transform' in SVGElement.prototype)) {
  Object.defineProperty(SVGElement.prototype, 'transform', {
    configurable: true,
    get: () => ({ baseVal: { consolidate: () => null } }),
  });
}

const VIEWPORT = { width: 900, height: 600 };

/** ResizeObserver callbacks, so a resize can be fired deliberately. */
let observerCallbacks: ResizeObserverCallback[] = [];
let boxSize = { ...VIEWPORT };

beforeEach(() => {
  observerCallbacks = [];
  boxSize = { ...VIEWPORT };

  vi.spyOn(SVGElement.prototype, 'getBoundingClientRect').mockImplementation(
    () => ({
      width: boxSize.width, height: boxSize.height,
      x: 0, y: 0, top: 0, left: 0,
      right: boxSize.width, bottom: boxSize.height,
      toJSON: () => ({}),
    }) as DOMRect
  );

  vi.stubGlobal('ResizeObserver', class {
    constructor(callback: ResizeObserverCallback) {
      observerCallbacks.push(callback);
    }
    observe() { }
    unobserve() { }
    disconnect() { }
  });
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

/** Fire the observer the way the browser does after `observe()`. */
function fireResize(size?: { width: number; height: number }) {
  if (size) boxSize = size;
  for (const callback of observerCallbacks) {
    callback([] as unknown as ResizeObserverEntry[], {} as ResizeObserver);
  }
}

function node(move_san: string, games: number, children?: OpeningNode[]): OpeningNode {
  return {
    move_san, ply: 0, games_count: games, wins: games, draws: 0, losses: 0,
    win_rate: 100, eco: null, opening_name: null,
    ...(children ? { children } : {}),
  };
}

const TREE = node('Start', 9, [
  node('e4', 5, [node('c5', 3), node('e5', 2)]),
  node('d4', 4, [node('Nf6', 4)]),
]);

/** The transform d3-zoom has applied to the content group. */
function transformOf(container: HTMLElement): string {
  return container.querySelector('svg > g')?.getAttribute('transform') ?? '';
}

function renderGraph(data: OpeningNode = TREE) {
  const ref = createRef<OpeningGraphHandle>();
  const view = render(
    <OpeningGraph data={data} onNodeHover={vi.fn()} onNodeHoverEnd={vi.fn()} graphRef={ref} />
  );
  return { ...view, ref };
}

/** A real ctrl+wheel gesture — d3 records sourceEvent, marking the view chosen. */
function userZooms(container: HTMLElement) {
  const svg = container.querySelector('svg')!;
  svg.dispatchEvent(new WheelEvent('wheel', {
    deltaY: -240, ctrlKey: true, bubbles: true, cancelable: true,
    clientX: 450, clientY: 300,
  }));
}

describe('OpeningGraph — fit', () => {
  it('applies a real transform rather than resetting to identity', () => {
    const { container } = renderGraph();

    // "Fit to view" used to call zoom.transform(zoomIdentity) — a reset, which
    // left an over-shrunk graph exactly as illegible as before.
    const transform = transformOf(container);
    expect(transform).not.toBe('');
    expect(transform).not.toBe('translate(0,0) scale(1)');
    expect(transform).toMatch(/scale\(/);
  });

  it('never shrinks past the readable floor', () => {
    const { container } = renderGraph();

    const scale = Number(/scale\(([\d.]+)\)/.exec(transformOf(container))?.[1]);
    // Fitting a wide tree into a short panel once drove 13px labels to ~4.6px.
    expect(scale).toBeGreaterThanOrEqual(0.72);
  });

  it('re-fits on demand after the user has moved the view', async () => {
    const { container, ref } = renderGraph();
    const fitted = transformOf(container);

    userZooms(container);
    expect(transformOf(container)).not.toBe(fitted);

    // fitToView animates, so the transform lands over the transition rather
    // than on the call.
    ref.current!.fitToView();
    await waitFor(() => expect(transformOf(container)).toBe(fitted));
  });
});

describe('OpeningGraph — zoom preserved across a rebuild', () => {
  it('restores the remembered transform instead of re-fitting', () => {
    const { container, rerender } = renderGraph();
    userZooms(container);
    const chosen = transformOf(container);

    // A refresh hands down an equal-but-new object, re-running the whole effect.
    rerender(
      <OpeningGraph
        data={JSON.parse(JSON.stringify(TREE))}
        onNodeHover={vi.fn()}
        onNodeHoverEnd={vi.fn()}
      />
    );

    expect(transformOf(container)).toBe(chosen);
  });

  it('is not clobbered by the observer’s immediate first callback', () => {
    const { container, rerender } = renderGraph();
    userZooms(container);
    const chosen = transformOf(container);

    rerender(
      <OpeningGraph
        data={JSON.parse(JSON.stringify(TREE))}
        onNodeHover={vi.fn()}
        onNodeHoverEnd={vi.fn()}
      />
    );
    // `observe()` delivers a callback for the current size straight away. Left
    // unguarded it refitted over the transform just restored — undoing zoom
    // preservation on every single refresh.
    fireResize();

    expect(transformOf(container)).toBe(chosen);
  });
});

describe('OpeningGraph — resize', () => {
  it('ignores a callback that reports the same size', () => {
    const { container } = renderGraph();
    // Collapsing changes the content without ever touching the zoom, so
    // `userAdjustedRef` stays false and the size check is the only thing left
    // guarding the view. A same-size callback — which `observe()` delivers
    // immediately, and which fires again for any spurious relayout — must not
    // be taken as a reason to re-place a graph the user is reading.
    fireEvent.click(container.querySelector('[role="treeitem"][aria-expanded="true"]')!);
    const before = transformOf(container);

    fireResize();

    expect(transformOf(container)).toBe(before);
  });

  it('refits when the panel actually changes size', () => {
    const { container } = renderGraph();
    const before = transformOf(container);

    fireResize({ width: 400, height: 300 });

    expect(transformOf(container)).not.toBe(before);
  });

  it('leaves a view the user chose by hand alone', () => {
    const { container } = renderGraph();
    userZooms(container);
    const chosen = transformOf(container);

    fireResize({ width: 400, height: 300 });

    // Rotating a phone must not throw away the line they were reading.
    expect(transformOf(container)).toBe(chosen);
  });

  it('counts the zoom buttons as a chosen view', () => {
    const { container, ref } = renderGraph();
    ref.current!.zoomIn();
    const chosen = transformOf(container);

    fireResize({ width: 400, height: 300 });

    // d3 reports sourceEvent: null for a programmatic transform, so the zoom
    // handler cannot tell a button press from our own fit — the handle marks it.
    expect(transformOf(container)).toBe(chosen);
  });

  it('hands the view back after a fit clicked straight after a wheel', async () => {
    const { container, ref } = renderGraph();
    const fitted = transformOf(container);
    userZooms(container);

    ref.current!.fitToView();
    await waitFor(() => expect(transformOf(container)).toBe(fitted));
    // fitToView animates; a resize fired mid-transition is simply overwritten
    // when the animation lands, so let it finish before testing the guard.
    await new Promise(resolve => setTimeout(resolve, 600));

    fireResize({ width: 320, height: 240 });

    // Asking to fit is asking us to manage the view again — the next resize
    // must move the graph rather than being treated as a hand-chosen view.
    // The Fit here lands inside d3-zoom's 150ms wheel-idle window, so our own
    // transform came back carrying the wheel's sourceEvent and re-marked the
    // view as hand-chosen; resize-refitting then stayed dead for good.
    expect(transformOf(container)).not.toBe(fitted);
  });
});
