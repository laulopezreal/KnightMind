import { useEffect, useRef, useImperativeHandle } from 'react';
import * as d3 from 'd3';
import type { OpeningNode } from '../api';
import { getScoreColor } from '../utils/openings';

const LAYOUT = {
  /** Vertical gap between sibling nodes, in layout units. */
  nodeSpacing: 34,
  /** Horizontal gap between plies, in layout units. */
  levelWidth: 155,
  /** Room reserved around the tree when fitting, in CSS px. Top clears the
   *  zoom toolbar and bottom the help hint, both of which float over the
   *  canvas and would otherwise cover nodes. */
  fitPadding: { top: 60, right: 32, bottom: 52, left: 32 },
  /** Approximate label overhang either side of a node, in layout units. */
  labelBleed: { left: 64, right: 104 },
  zoomExtent: [0.35, 3] as [number, number],
  /**
   * Auto-fit never shrinks below this. Fitting a wide tree into a short panel
   * used to drive the 13px labels down to ~4.6px; past this point, panning a
   * readable tree beats seeing an illegible whole.
   */
  minFitScale: 0.72,
  /** Score ring geometry, in layout units, measured out from the node edge. */
  ring: { gap: 2.5, width: 2 },
  linkStrokeWidth: 1.5,
  linkStrokeOpacity: 0.2,
  labelFontSize: '13px',
  labelOffset: 20,
  separation: { sibling: 1, cousin: 1.25 },
  transitionDuration: 400,
  /** Trees at or above this size open partly collapsed. */
  autoCollapseThreshold: 40,
  /** Everything at or below this depth starts collapsed. */
  autoCollapseDepth: 3,
} as const;

/** Viewport coordinates a tooltip should be anchored to. */
export interface NodeAnchor {
  x: number;
  y: number;
}

/** Handle exposed to parent via ref for toolbar controls */
export interface OpeningGraphHandle {
  fitToView: () => void;
  zoomIn: () => void;
  zoomOut: () => void;
}

/** Extended hierarchy node with collapsible _children storage and a stable key */
type CollapsibleNode = d3.HierarchyPointNode<OpeningNode> & {
  _children?: d3.HierarchyPointNode<OpeningNode>[];
  /** Full root-to-node move path — see `assignKeys`. */
  key?: string;
};

/**
 * Give every node a key that is unique across the whole tree.
 *
 * `move_san + depth + parent.move_san` is not: `1.e4 c5 2.Nf3 Nc6 3.d4 cxd4`
 * and `1.e4 c5 2.Nf3 d6 3.d4 cxd4` produce the same triple with different
 * statistics. Colliding keys made d3 destroy and re-create those nodes on
 * every expand/collapse — they blinked and flew in from the parent instead of
 * gliding — and left the join one refactor away from binding a node to another
 * line's data. A full move path cannot collide.
 */
function assignKeys(root: CollapsibleNode): void {
  (function walk(node: CollapsibleNode, prefix: string) {
    node.key = prefix ? `${prefix}>${node.data.move_san}` : node.data.move_san;
    for (const child of node.children ?? []) {
      walk(child as CollapsibleNode, node.key);
    }
  })(root, '');
}

/**
 * Collapse every node at or below `depth`, deepest first.
 *
 * `root.each()` reads `node.children` *after* invoking the callback, so
 * clearing children mid-traversal pruned the iterator's own descent: only
 * depth-3 nodes were ever collapsed, and one click on one of them dumped the
 * entire ten-ply subtree at once. Recursing before mutating collapses every
 * level, which is what makes progressive disclosure work.
 */
function collapseFrom(node: CollapsibleNode, depth: number): void {
  for (const child of node.children ?? []) {
    collapseFrom(child as CollapsibleNode, depth);
  }
  if (node.depth >= depth && node.children) {
    node._children = node.children;
    node.children = undefined;
  }
}

/**
 * Record which nodes are currently open, so the state can be reapplied after a
 * rebuild. Only visible branches need walking: `collapseFrom` works
 * depth-first, so anything under a collapsed node is collapsed too.
 */
function collectExpanded(node: CollapsibleNode, out: Set<string>): void {
  if (!node.children?.length) return;
  if (node.key) out.add(node.key);
  for (const child of node.children) collectExpanded(child as CollapsibleNode, out);
}

/**
 * Reapply a recorded expansion state to a freshly built hierarchy. Descends
 * through collapsed branches as well, so deep state is preserved even where it
 * is not currently on screen.
 */
function applyExpansion(node: CollapsibleNode, keys: Set<string>): void {
  const children = node.children ?? node._children;
  if (!children?.length) return;

  if (node.key && keys.has(node.key)) {
    node.children = children;
    node._children = undefined;
  } else {
    node._children = children;
    node.children = undefined;
  }
  for (const child of children) applyExpansion(child as CollapsibleNode, keys);
}

/** Node radius — area tracks games played, clamped so the extremes stay usable. */
function radiusFor(node: CollapsibleNode): number {
  return Math.max(6, Math.min(16, Math.sqrt(node.data.games_count) * 1.5 + 4));
}

/** Visible nodes in top-to-bottom reading order (also the arrow-key order). */
function visibleNodes(root: CollapsibleNode): CollapsibleNode[] {
  const out: CollapsibleNode[] = [];
  (function walk(node: CollapsibleNode) {
    out.push(node);
    for (const child of node.children ?? []) walk(child as CollapsibleNode);
  })(root);
  return out;
}

function hasHiddenChildren(node: CollapsibleNode): boolean {
  return Boolean(node._children?.length);
}

function isExpanded(node: CollapsibleNode): boolean {
  return Boolean(node.children?.length);
}

function isExpandable(node: CollapsibleNode): boolean {
  return isExpanded(node) || hasHiddenChildren(node);
}

/** Format node label with chess move number: "1. e4" (white) or "1…e5" (black) */
function formatMoveLabel(node: d3.HierarchyNode<OpeningNode>): string {
  if (node.data.move_san === 'Start') return 'Start';
  const moveNum = Math.ceil(node.depth / 2);
  const isWhite = node.depth % 2 === 1;
  return isWhite ? `${moveNum}. ${node.data.move_san}` : `${moveNum}…${node.data.move_san}`;
}

/**
 * Spoken description of a node. The score is spelled out as a number because
 * it is otherwise carried by fill colour alone — unavailable to screen reader
 * users and ambiguous under red/green colour blindness.
 */
function describeNode(node: CollapsibleNode): string {
  const d = node.data;
  const label = d.move_san === 'Start' ? 'Starting position' : formatMoveLabel(node);
  return `${label}. ${d.games_count} games, ${d.wins} won, ${d.draws} drawn, ${d.losses} lost, score ${d.win_rate}%`;
}

interface OpeningGraphProps {
  data: OpeningNode;
  /** Fired on pointer hover *and* keyboard focus, so the stats are not hover-only. */
  onNodeHover: (anchor: NodeAnchor, node: OpeningNode) => void;
  onNodeHoverEnd: () => void;
  /**
   * Fired when a node is activated (click or Enter/Space) with the whole
   * root-to-node path, so the page can name the line and offer actions on it.
   * Called with null when the selection is cleared.
   */
  onNodeSelect?: (path: OpeningNode[] | null) => void;
  onError?: (message: string) => void;
  graphRef?: React.RefObject<OpeningGraphHandle | null>;
}

export function OpeningGraph({ data, onNodeHover, onNodeHoverEnd, onNodeSelect, onError, graphRef }: OpeningGraphProps) {
  const svgRef = useRef<SVGSVGElement>(null);
  const gRef = useRef<SVGGElement>(null);
  const zoomBehaviorRef = useRef<d3.ZoomBehavior<SVGSVGElement, unknown> | null>(null);
  const fitRef = useRef<(animate?: boolean) => void>(() => {});
  const callbacksRef = useRef({ onNodeHover, onNodeHoverEnd, onNodeSelect, onError });
  callbacksRef.current = { onNodeHover, onNodeHoverEnd, onNodeSelect, onError };

  // Survive a rebuild. A refresh or colour-filter change hands down new `data`,
  // which re-runs the effect from scratch; without carrying these across, every
  // refresh silently threw away the user's expanded lines and their zoom.
  const expandedKeysRef = useRef<Set<string>>(new Set());
  const transformRef = useRef<d3.ZoomTransform | null>(null);
  const focusedKeyRef = useRef<string | null>(null);
  /** True once the user has zoomed or panned by hand, rather than us fitting. */
  const userAdjustedRef = useRef(false);

  useImperativeHandle(graphRef, () => ({
    // Actually fits the visible tree to the panel. This used to reset the zoom
    // transform to identity, which left an over-shrunk graph exactly as
    // illegible as before — the one control meant to rescue it did nothing.
    fitToView: () => {
      // Asking to fit is asking to hand the view back to us, so resize-refitting
      // resumes from here.
      userAdjustedRef.current = false;
      fitRef.current(true);
    },
    zoomIn: () => {
      const svgEl = svgRef.current;
      const zoom = zoomBehaviorRef.current;
      if (!svgEl || !zoom) return;
      d3.select(svgEl).transition().duration(300).call(zoom.scaleBy as never, 1.3);
    },
    zoomOut: () => {
      const svgEl = svgRef.current;
      const zoom = zoomBehaviorRef.current;
      if (!svgEl || !zoom) return;
      d3.select(svgEl).transition().duration(300).call(zoom.scaleBy as never, 0.7);
    },
  }), []);

  useEffect(() => {
    const svgEl = svgRef.current;
    const gEl = gRef.current;
    if (!svgEl || !gEl) return;

    const gSelection = d3.select(gEl);
    gSelection.selectAll('*').remove();

    // Honour the OS setting rather than animating regardless.
    const reducedMotion = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false;
    const duration = reducedMotion ? 0 : LAYOUT.transitionDuration;

    let disconnectResize: (() => void) | undefined;

    try {
      const root = d3.hierarchy(data) as CollapsibleNode;
      assignKeys(root);

      // A refresh or filter change re-runs this effect with new data. On the
      // first build, auto-collapse decides what is open and we record it; on a
      // rebuild the recorded set is authoritative, so the user's expanded lines
      // survive instead of snapping back to the default three plies.
      const isRebuild = expandedKeysRef.current.size > 0;
      if (isRebuild) {
        applyExpansion(root, expandedKeysRef.current);
      } else {
        if (root.descendants().length >= LAYOUT.autoCollapseThreshold) {
          collapseFrom(root, LAYOUT.autoCollapseDepth);
        }
        collectExpanded(root, expandedKeysRef.current);
      }

      const contentG = gSelection.append('g');
      // The link layer carries no information of its own and would otherwise be
      // an invalid non-treeitem child of role="tree".
      contentG.append('g').attr('class', 'links').attr('aria-hidden', 'true');
      contentG.append('g').attr('class', 'nodes');

      // nodeSize (not size) keeps spacing constant as the tree grows; the zoom
      // transform, not a rescaled viewBox, decides how much is on screen.
      const treeLayout = d3.tree<OpeningNode>()
        .nodeSize([LAYOUT.nodeSpacing, LAYOUT.levelWidth])
        .separation((a, b) => (a.parent === b.parent ? LAYOUT.separation.sibling : LAYOUT.separation.cousin));

      const linkPathGen = d3.linkHorizontal<
        d3.HierarchyPointLink<OpeningNode>,
        d3.HierarchyPointNode<OpeningNode>
      >()
        .x(d => d.y)
        .y(d => d.x);

      /**
       * Ring around each node whose sweep is the score. Redundant with fill
       * colour on purpose: the palette runs red to green, the worst possible
       * pairing for the most common colour blindness, so the same figure is
       * also carried by an angle that does not depend on hue at all.
       */
      const scoreArc = d3.arc<{ inner: number; outer: number; end: number }>()
        .innerRadius(d => d.inner)
        .outerRadius(d => d.outer)
        .startAngle(0)
        .endAngle(d => d.end);

      function ringPath(d: CollapsibleNode, full: boolean): string {
        const r = radiusFor(d);
        const score = Math.min(100, Math.max(0, d.data.win_rate));
        return scoreArc({
          inner: r + LAYOUT.ring.gap,
          outer: r + LAYOUT.ring.gap + LAYOUT.ring.width,
          end: full ? 2 * Math.PI : (2 * Math.PI * score) / 100,
        }) ?? '';
      }

      /** Node owning the single tab stop (roving tabindex). */
      let focusedKey: string | null = focusedKeyRef.current ?? root.key ?? null;

      function anchorFor(group: SVGGElement): NodeAnchor {
        const rect = group.getBoundingClientRect();
        return { x: rect.right + 8, y: rect.top + rect.height / 2 };
      }

      function toggleNode(d: CollapsibleNode): void {
        if (d._children) {
          d.children = d._children;
          d._children = undefined;
          if (d.key) expandedKeysRef.current.add(d.key);
        } else if (d.children) {
          d._children = d.children;
          d.children = undefined;
          if (d.key) expandedKeysRef.current.delete(d.key);
        }
      }

      /** Root-to-node path as plain data, for the page's selection panel. */
      function reportSelection(d: CollapsibleNode): void {
        callbacksRef.current.onNodeSelect?.(
          d.ancestors().reverse().map(ancestor => ancestor.data)
        );
      }

      function focusNode(target: CollapsibleNode | undefined): void {
        if (!target?.key) return;
        focusedKey = target.key;
        focusedKeyRef.current = focusedKey;
        update(target);
        // Matched on the bound datum rather than a `[data-key=...]` selector:
        // SAN carries `+`, `#` and `=`, which need escaping in a selector.
        contentG.select('g.nodes')
          .selectAll<SVGGElement, CollapsibleNode>('g.node')
          .filter(d => d.key === target.key)
          .node()
          ?.focus();
      }

      /**
       * WAI-ARIA tree keyboard model. Without it the graph had zero focusable
       * elements: a keyboard user could reach the zoom buttons but never a
       * single move, leaving every per-move statistic unreachable.
       */
      function onKeyDown(event: KeyboardEvent, d: CollapsibleNode): void {
        const order = visibleNodes(root);
        const index = order.indexOf(d);
        let handled = true;

        switch (event.key) {
          case 'ArrowDown':
            focusNode(order[index + 1]);
            break;
          case 'ArrowUp':
            focusNode(order[index - 1]);
            break;
          case 'ArrowRight':
            if (hasHiddenChildren(d)) {
              toggleNode(d);
              focusNode(d);
            } else if (isExpanded(d)) {
              focusNode(d.children![0] as CollapsibleNode);
            }
            break;
          case 'ArrowLeft':
            if (isExpanded(d)) {
              toggleNode(d);
              focusNode(d);
            } else if (d.parent) {
              focusNode(d.parent as CollapsibleNode);
            }
            break;
          case 'Home':
            focusNode(order[0]);
            break;
          case 'End':
            focusNode(order[order.length - 1]);
            break;
          case 'Enter':
          case ' ':
            // Selecting works on any node; expanding only where there is more
            // to show. Keeping them on one key means keyboard users reach the
            // line's actions the same way pointer users do.
            if (isExpandable(d)) toggleNode(d);
            focusNode(d);
            reportSelection(d);
            break;
          default:
            handled = false;
        }

        if (handled) {
          event.preventDefault();
          event.stopPropagation();
        }
      }

      function updateLinks(source: CollapsibleNode, links: d3.HierarchyPointLink<OpeningNode>[]): void {
        const linkSel = contentG.select('g.links')
          .selectAll<SVGPathElement, d3.HierarchyPointLink<OpeningNode>>('path.link')
          .data(links, (d) => (d.target as CollapsibleNode).key!);

        const collapsedPath = () => {
          const o = { x: source.x, y: source.y } as d3.HierarchyPointNode<OpeningNode>;
          return linkPathGen({ source: o, target: o } as d3.HierarchyPointLink<OpeningNode>);
        };

        const linkEnter = linkSel.enter()
          .append('path')
          .attr('class', 'link')
          .attr('fill', 'none')
          .attr('stroke', 'currentColor')
          .attr('stroke-width', LAYOUT.linkStrokeWidth)
          .attr('stroke-opacity', 0)
          .attr('d', collapsedPath);

        linkEnter.merge(linkSel)
          .transition()
          .duration(duration)
          .attr('stroke-opacity', LAYOUT.linkStrokeOpacity)
          .attr('d', linkPathGen);

        linkSel.exit()
          .transition()
          .duration(duration)
          .attr('stroke-opacity', 0)
          .attr('d', collapsedPath)
          .remove();
      }

      function updateNodes(source: CollapsibleNode, nodes: CollapsibleNode[]): void {
        const nodeSel = contentG.select('g.nodes')
          .selectAll<SVGGElement, CollapsibleNode>('g.node')
          .data(nodes, (d) => d.key!);

        const nodeEnter = nodeSel.enter()
          .append('g')
          .attr('class', 'node')
          .attr('data-key', d => d.key!)
          .attr('role', 'treeitem')
          .attr('transform', `translate(${source.y},${source.x})`)
          .style('cursor', 'pointer')
          .style('opacity', 0)
          .style('outline', 'none');

        nodeEnter.append('circle')
          .attr('class', 'node-dot')
          .attr('stroke', 'var(--bg-primary)')
          .attr('stroke-width', 2);

        // Full-circle track, so a short sweep reads as "low score" rather than
        // as a rendering glitch.
        nodeEnter.append('path')
          .attr('class', 'score-track')
          .attr('fill', 'currentColor')
          .attr('fill-opacity', 0.12)
          .attr('pointer-events', 'none')
          .attr('aria-hidden', 'true');

        nodeEnter.append('path')
          .attr('class', 'score-ring')
          .attr('fill', 'currentColor')
          .attr('fill-opacity', 0.75)
          .attr('pointer-events', 'none')
          .attr('aria-hidden', 'true');

        nodeEnter.append('text')
          .attr('class', 'node-label')
          .attr('dy', 5)
          .attr('fill', 'currentColor')
          .attr('stroke', 'var(--bg-primary)')
          .attr('stroke-width', 4)
          .attr('paint-order', 'stroke')
          .attr('font-size', LAYOUT.labelFontSize)
          .attr('font-family', 'Inter, sans-serif')
          .attr('font-weight', '500')
          .attr('aria-hidden', 'true');

        nodeEnter.append('text')
          .attr('class', 'collapse-indicator')
          .attr('dy', '0.35em')
          .attr('text-anchor', 'middle')
          .attr('fill', 'var(--bg-primary)')
          .attr('font-size', '10px')
          .attr('font-family', 'Inter, sans-serif')
          .attr('font-weight', '700')
          .attr('pointer-events', 'none')
          .attr('aria-hidden', 'true');

        // A plain click, not a drag gesture. Node dragging detached nodes from
        // the layout only for them to snap back on the next update, and routed
        // expand/collapse through mousedown/mouseup — which no keyboard or
        // assistive-technology activation produces.
        nodeEnter
          .on('click', function (event: MouseEvent, d) {
            event.stopPropagation();
            if (isExpandable(d)) toggleNode(d);
            focusedKey = d.key ?? null;
            focusedKeyRef.current = focusedKey;
            update(d);
            (this as SVGGElement).focus();
            reportSelection(d);
          })
          .on('keydown', function (event: KeyboardEvent, d) {
            onKeyDown(event, d);
          })
          .on('mouseenter', function (_event: MouseEvent, d) {
            callbacksRef.current.onNodeHover(anchorFor(this as SVGGElement), d.data);
          })
          .on('mouseleave', () => callbacksRef.current.onNodeHoverEnd())
          // Focus mirrors hover, so the statistics are reachable by keyboard.
          .on('focus', function (_event: FocusEvent, d) {
            callbacksRef.current.onNodeHover(anchorFor(this as SVGGElement), d.data);
          })
          .on('blur', () => callbacksRef.current.onNodeHoverEnd());

        // The roving tab stop must always land somewhere. A refetch can drop
        // the previously focused line entirely (games removed, archive
        // re-imported), and every node then scored `tabindex="-1"` — leaving the
        // whole tree unreachable by Tab, which is the exact failure the ARIA
        // work set out to fix.
        if (!nodes.some(d => d.key === focusedKey)) {
          focusedKey = root.key ?? null;
          focusedKeyRef.current = focusedKey;
        }

        const nodeUpdate = nodeEnter.merge(nodeSel);

        nodeUpdate
          .attr('tabindex', d => (d.key === focusedKey ? 0 : -1))
          .attr('aria-level', d => d.depth + 1)
          .attr('aria-setsize', d => d.parent?.children?.length ?? 1)
          .attr('aria-posinset', d => (d.parent?.children?.indexOf(d) ?? 0) + 1)
          .attr('aria-label', d => describeNode(d))
          .attr('aria-expanded', d => (isExpandable(d) ? String(isExpanded(d)) : null));

        nodeUpdate.transition()
          .duration(duration)
          .attr('transform', d => `translate(${d.y},${d.x})`)
          .style('opacity', 1);

        nodeUpdate.select('circle.node-dot')
          .attr('r', d => radiusFor(d))
          .attr('fill', d => getScoreColor(d.data.win_rate))
          .attr('stroke', d => (d.key === focusedKey ? 'var(--text-primary)' : 'var(--bg-primary)'))
          .attr('stroke-width', d => (d.key === focusedKey ? 3 : 2));

        nodeUpdate.select('path.score-track').attr('d', d => ringPath(d, true));
        nodeUpdate.select('path.score-ring').attr('d', d => ringPath(d, false));

        nodeUpdate.select('text.collapse-indicator')
          .text(d => (hasHiddenChildren(d) ? '+' : ''));

        nodeUpdate.select('text.node-label')
          .attr('x', d => (isExpanded(d) ? -LAYOUT.labelOffset : LAYOUT.labelOffset))
          .attr('text-anchor', d => (isExpanded(d) ? 'end' : 'start'))
          .text(d => formatMoveLabel(d));

        nodeSel.exit()
          .transition()
          .duration(duration)
          .attr('transform', `translate(${source.y},${source.x})`)
          .style('opacity', 0)
          .remove();
      }

      function update(source: CollapsibleNode): void {
        treeLayout(root);
        updateLinks(source, root.links() as d3.HierarchyPointLink<OpeningNode>[]);
        updateNodes(source, root.descendants() as CollapsibleNode[]);
      }

      /** Transform that centres the visible tree in the panel, never below minFitScale. */
      function fit(animate = false): void {
        const zoom = zoomBehaviorRef.current;
        if (!zoom) return;
        const { width, height } = svgEl!.getBoundingClientRect();
        if (!width || !height) return;

        const nodes = visibleNodes(root);
        if (!nodes.length) return;

        const minX = Math.min(...nodes.map(n => n.y)) - LAYOUT.labelBleed.left;
        const maxX = Math.max(...nodes.map(n => n.y)) + LAYOUT.labelBleed.right;
        const minY = Math.min(...nodes.map(n => n.x)) - LAYOUT.nodeSpacing / 2;
        const maxY = Math.max(...nodes.map(n => n.x)) + LAYOUT.nodeSpacing / 2;

        const pad = LAYOUT.fitPadding;
        const availW = Math.max(1, width - pad.left - pad.right);
        const availH = Math.max(1, height - pad.top - pad.bottom);
        const contentW = Math.max(1, maxX - minX);
        const contentH = Math.max(1, maxY - minY);

        const k = Math.min(
          1,
          Math.max(LAYOUT.minFitScale, Math.min(availW / contentW, availH / contentH))
        );
        // Centre when it fits; anchor top-left (keeping the root in view) when
        // the readable-scale floor means it does not.
        const tx = pad.left - minX * k + Math.max(0, (availW - contentW * k) / 2);
        const ty = pad.top - minY * k + Math.max(0, (availH - contentH * k) / 2);
        const transform = d3.zoomIdentity.translate(tx, ty).scale(k);

        const target = d3.select(svgEl!);
        (animate && !reducedMotion ? target.transition().duration(500) : target)
          .call(zoom.transform as never, transform);
      }
      fitRef.current = fit;

      const zoomBehavior = d3.zoom<SVGSVGElement, unknown>()
        .scaleExtent(LAYOUT.zoomExtent)
        // Stated explicitly rather than left to d3's default, which reads
        // `svg.width.baseVal` — an attribute this SVG sizes in CSS, not markup.
        .extent((): [[number, number], [number, number]] => {
          const { width, height } = svgEl!.getBoundingClientRect();
          return [[0, 0], [width || 1, height || 1]];
        })
        // Plain wheel scrolling belongs to the page: the graph used to
        // preventDefault every wheel event, so scrolling down toward the legend
        // zoomed the tree instead. One-finger touch likewise stays with the
        // page; two fingers pan and pinch the graph.
        .filter((event: Event) => {
          if (event.type === 'wheel') {
            const e = event as WheelEvent;
            return e.ctrlKey || e.metaKey;
          }
          if (event.type === 'touchstart') {
            return (event as TouchEvent).touches.length >= 2;
          }
          return !(event as MouseEvent).button;
        })
        .on('start', () => d3.select(svgEl).style('cursor', 'grabbing'))
        .on('zoom', (event: d3.D3ZoomEvent<SVGSVGElement, unknown>) => {
          gSelection.attr('transform', event.transform.toString());
          // Remembered so a refresh does not throw away where the user is
          // looking, alongside which lines they had open.
          transformRef.current = event.transform;
          // `sourceEvent` is null for programmatic transforms (our own fit) and
          // set for real gestures, which is what distinguishes "the user chose
          // this view" from "we placed it".
          if (event.sourceEvent) userAdjustedRef.current = true;
        })
        .on('end', () => d3.select(svgEl).style('cursor', 'grab'));

      zoomBehaviorRef.current = zoomBehavior;
      d3.select(svgEl).call(zoomBehavior);

      update(root);
      if (isRebuild && transformRef.current) {
        d3.select(svgEl).call(zoomBehavior.transform as never, transformRef.current);
      } else {
        fit(false);
      }

      // Refit on container resize, so rotating a phone or opening the sidebar
      // does not strand the tree off-screen.
      //
      // Two guards, both load-bearing. `observe()` delivers an immediate
      // callback for the current size, which would have refitted right over the
      // transform just restored above — silently undoing zoom preservation on
      // every refresh. And once the user has chosen their own view, a resize
      // must not yank it away; the Fit control is there when they want it back.
      let lastSize = svgEl.getBoundingClientRect();
      const observer = new ResizeObserver(() => {
        const size = svgEl!.getBoundingClientRect();
        const unchanged =
          Math.abs(size.width - lastSize.width) < 1 &&
          Math.abs(size.height - lastSize.height) < 1;
        lastSize = size;
        if (unchanged || userAdjustedRef.current) return;
        fit(false);
      });
      observer.observe(svgEl);
      disconnectResize = () => observer.disconnect();
    } catch (e) {
      callbacksRef.current.onError?.(
        e instanceof Error ? e.message : 'Failed to draw opening tree.'
      );
    }

    return () => {
      disconnectResize?.();
      d3.select(svgEl).on('.zoom', null);
      d3.select(gEl).selectAll('*').remove();
      zoomBehaviorRef.current = null;
      fitRef.current = () => {};
    };
  }, [data]);

  return (
    <svg
      ref={svgRef}
      className="text-primary block w-full h-full"
      style={{ cursor: 'grab' }}
      role="tree"
      aria-label="Opening repertoire tree. Use the arrow keys to move between moves and expand lines."
    >
      <g ref={gRef} />
    </svg>
  );
}
