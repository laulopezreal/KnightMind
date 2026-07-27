import { useEffect, useRef, useImperativeHandle } from 'react';
import * as d3 from 'd3';
import type { OpeningNode } from '../api';
import { getScoreColor } from '../utils/openings';

const LAYOUT = {
  nodeSpacing: 35,
  levelWidth: 180,
  minWidth: 800,
  minHeight: 400,
  margin: { top: 30, right: 150, bottom: 30, left: 60 },
  zoomExtent: [0.3, 3] as [number, number],
  linkStrokeWidth: 1.5,
  linkStrokeOpacity: 0.2,
  labelFontSize: '13px',
  labelOffset: 20,
  separation: { sibling: 1, cousin: 1.2 },
  transitionDuration: 400,
  autoCollapseThreshold: 50,
  autoCollapseDepth: 3,
} as const;

/** Handle exposed to parent via ref for toolbar controls */
export interface OpeningGraphHandle {
  fitToView: () => void;
  zoomIn: () => void;
  zoomOut: () => void;
}

/** Extended hierarchy node with collapsible _children storage */
type CollapsibleNode = d3.HierarchyPointNode<OpeningNode> & {
  _children?: d3.HierarchyPointNode<OpeningNode>[];
};

function countLeaves(node: d3.HierarchyNode<OpeningNode>): number {
  if (!node.children || node.children.length === 0) return 1;
  return node.children.reduce((sum, child) => sum + countLeaves(child), 0);
}

function getMaxDepth(node: d3.HierarchyNode<OpeningNode>, depth = 0): number {
  if (!node.children || node.children.length === 0) return depth;
  return Math.max(...node.children.map((child) => getMaxDepth(child, depth + 1)));
}

/** Count visible nodes for layout sizing */
function countVisibleLeaves(node: CollapsibleNode): number {
  if (!node.children || node.children.length === 0) return 1;
  return node.children.reduce((sum, child) => sum + countVisibleLeaves(child as CollapsibleNode), 0);
}

function getVisibleMaxDepth(node: CollapsibleNode, depth = 0): number {
  if (!node.children || node.children.length === 0) return depth;
  return Math.max(...node.children.map((child) => getVisibleMaxDepth(child as CollapsibleNode, depth + 1)));
}

/** Format node label with chess move number: "1. e4" (white) or "1…e5" (black) */
function formatMoveLabel(node: d3.HierarchyNode<OpeningNode>): string {
  if (node.data.move_san === 'Start') return '●';
  const moveNum = Math.ceil(node.depth / 2);
  const isWhite = node.depth % 2 === 1;
  return isWhite ? `${moveNum}. ${node.data.move_san}` : `${moveNum}\u2026${node.data.move_san}`;
}

interface OpeningGraphProps {
  data: OpeningNode;
  onNodeHover: (event: MouseEvent, node: OpeningNode) => void;
  onNodeHoverEnd: () => void;
  onError?: (message: string) => void;
  graphRef?: React.RefObject<OpeningGraphHandle | null>;
}

export function OpeningGraph({ data, onNodeHover, onNodeHoverEnd, onError, graphRef }: OpeningGraphProps) {
  const svgRef = useRef<SVGSVGElement>(null);
  const gRef = useRef<SVGGElement>(null);
  const zoomBehaviorRef = useRef<d3.ZoomBehavior<SVGSVGElement, unknown> | null>(null);
  const callbacksRef = useRef({ onNodeHover, onNodeHoverEnd, onError });
  callbacksRef.current = { onNodeHover, onNodeHoverEnd, onError };

  useImperativeHandle(graphRef, () => ({
    fitToView: () => {
      const svgEl = svgRef.current;
      const zoom = zoomBehaviorRef.current;
      if (!svgEl || !zoom) return;
      d3.select(svgEl)
        .transition()
        .duration(500)
        .call(zoom.transform as never, d3.zoomIdentity);
    },
    zoomIn: () => {
      const svgEl = svgRef.current;
      const zoom = zoomBehaviorRef.current;
      if (!svgEl || !zoom) return;
      d3.select(svgEl)
        .transition()
        .duration(300)
        .call(zoom.scaleBy as never, 1.3);
    },
    zoomOut: () => {
      const svgEl = svgRef.current;
      const zoom = zoomBehaviorRef.current;
      if (!svgEl || !zoom) return;
      d3.select(svgEl)
        .transition()
        .duration(300)
        .call(zoom.scaleBy as never, 0.7);
    },
  }), []);

  useEffect(() => {
    const svgEl = svgRef.current;
    const gEl = gRef.current;
    if (!svgEl || !gEl) return;

    // Clear previous D3 content
    const gSelection = d3.select(gEl);
    gSelection.selectAll('*').remove();

    try {
      const { margin } = LAYOUT;

      // Build hierarchy
      const root = d3.hierarchy(data) as CollapsibleNode;

      // Auto-collapse large trees at depth > 3
      const totalNodes = root.descendants().length;
      if (totalNodes >= LAYOUT.autoCollapseThreshold) {
        root.each((d) => {
          const node = d as CollapsibleNode;
          if (node.depth >= LAYOUT.autoCollapseDepth && node.children) {
            node._children = node.children;
            node.children = undefined;
          }
        });
      }

      // Calculate initial dimensions from tree shape
      const leafCount = countLeaves(root);
      const maxDepth = getMaxDepth(root);
      const width = Math.max(LAYOUT.minWidth, (maxDepth + 1) * LAYOUT.levelWidth + 100);
      const height = Math.max(LAYOUT.minHeight, leafCount * LAYOUT.nodeSpacing + 60);

      // Configure SVG with viewBox
      d3.select(svgEl)
        .attr('viewBox', `0 0 ${width} ${height}`)
        .attr('preserveAspectRatio', 'xMidYMid meet');

      // Content group offset by margins
      const contentG = gSelection.append('g')
        .attr('transform', `translate(${margin.left},${margin.top})`);

      // Create empty containers for links and nodes
      contentG.append('g').attr('class', 'links');
      contentG.append('g').attr('class', 'nodes');

      // Tree layout (recalculated on each update)
      const treeLayout = d3.tree<OpeningNode>()
        .separation((a, b) => (a.parent === b.parent ? LAYOUT.separation.sibling : LAYOUT.separation.cousin));

      // Link path generator
      const linkPathGen = d3.linkHorizontal<
        d3.HierarchyPointLink<OpeningNode>,
        d3.HierarchyPointNode<OpeningNode>
      >()
        .x(d => d.y)
        .y(d => d.x);

      // Track selected node
      let selectedNode: CollapsibleNode | null = null;

      // Track drag state to differentiate click vs drag
      let dragOccurred = false;

      // Toggle expand/collapse
      function toggleNode(d: CollapsibleNode): void {
        if (d._children) {
          d.children = d._children;
          d._children = undefined;
        } else if (d.children) {
          d._children = d.children;
          d.children = undefined;
        }
      }

      // Drag behavior on node groups
      const contentNode = contentG.node();
      const dragBehavior = d3.drag<SVGGElement, CollapsibleNode>()
        .container(function () { return contentNode as SVGGElement; })
        .on('start', function (event) {
          dragOccurred = false;
          event.sourceEvent.stopPropagation();
          d3.select(this).raise();
          d3.select(svgEl).style('cursor', 'grabbing');
          d3.select(this).style('cursor', 'grabbing');
        })
        .on('drag', function (event, d) {
          dragOccurred = true;
          d.y = event.x;
          d.x = event.y;
          d3.select(this).attr('transform', `translate(${d.y},${d.x})`);
          // Redraw all links with updated positions
          contentG.select('g.links')
            .selectAll<SVGPathElement, d3.HierarchyPointLink<OpeningNode>>('path')
            .attr('d', linkPathGen);
        })
        .on('end', function (_event, d) {
          d3.select(svgEl).style('cursor', 'grab');
          d3.select(this).style('cursor', 'pointer');
          if (!dragOccurred) {
            // Click (no drag movement) — toggle collapse and select
            selectedNode = d;
            toggleNode(d);
            update(d);
          }
        });

      /** Update link elements with enter/update/exit transitions */
      function updateLinks(
        source: CollapsibleNode,
        links: d3.HierarchyPointLink<OpeningNode>[],
        duration: number,
      ): void {
        const linkSel = contentG.select('g.links')
          .selectAll<SVGPathElement, d3.HierarchyPointLink<OpeningNode>>('path.link')
          .data(links, (d) => {
            const target = d.target as CollapsibleNode;
            return `${target.data.move_san}-${target.depth}-${target.parent?.data.move_san ?? 'root'}`;
          });

        // Enter: new links start at source position
        const linkEnter = linkSel.enter()
          .append('path')
          .attr('class', 'link')
          .attr('fill', 'none')
          .attr('stroke', 'currentColor')
          .attr('stroke-width', LAYOUT.linkStrokeWidth)
          .attr('stroke-opacity', 0)
          .attr('d', () => {
            const o = { x: source.x, y: source.y } as d3.HierarchyPointNode<OpeningNode>;
            return linkPathGen({ source: o, target: o } as d3.HierarchyPointLink<OpeningNode>);
          });

        // Merge: transition to final positions
        linkEnter.merge(linkSel)
          .transition()
          .duration(duration)
          .attr('stroke-opacity', LAYOUT.linkStrokeOpacity)
          .attr('d', linkPathGen);

        // Exit: collapse to source and remove
        linkSel.exit()
          .transition()
          .duration(duration)
          .attr('stroke-opacity', 0)
          .attr('d', () => {
            const o = { x: source.x, y: source.y } as d3.HierarchyPointNode<OpeningNode>;
            return linkPathGen({ source: o, target: o } as d3.HierarchyPointLink<OpeningNode>);
          })
          .remove();
      }

      /** Update node elements with enter/update/exit transitions */
      function updateNodes(
        source: CollapsibleNode,
        nodes: CollapsibleNode[],
        duration: number,
      ): void {
        const nodeSel = contentG.select('g.nodes')
          .selectAll<SVGGElement, CollapsibleNode>('g.node')
          .data(nodes, (d) => `${d.data.move_san}-${d.depth}-${d.parent?.data.move_san ?? 'root'}`);

        // Enter: new nodes start at source position
        const nodeEnter = nodeSel.enter()
          .append('g')
          .attr('class', 'node')
          .attr('transform', `translate(${source.y},${source.x})`)
          .style('cursor', 'pointer')
          .style('opacity', 0);

        // Circle
        nodeEnter.append('circle')
          .attr('r', d => Math.max(6, Math.min(16, Math.sqrt(d.data.games_count) * 1.5 + 4)))
          .attr('fill', d => getScoreColor(d.data.win_rate))
          .attr('stroke', 'var(--bg-primary)')
          .attr('stroke-width', 2);

        // Label
        nodeEnter.append('text')
          .attr('class', 'node-label')
          .attr('dy', 5)
          .attr('x', d => (d.children || d._children) ? -LAYOUT.labelOffset : LAYOUT.labelOffset)
          .attr('text-anchor', d => (d.children || d._children) ? 'end' : 'start')
          .attr('fill', 'currentColor')
          .attr('stroke', 'var(--bg-primary)')
          .attr('stroke-width', 4)
          .attr('paint-order', 'stroke')
          .attr('font-size', LAYOUT.labelFontSize)
          .attr('font-family', 'Inter, sans-serif')
          .attr('font-weight', '500')
          .text(d => formatMoveLabel(d));

        // Collapse indicator (+)
        nodeEnter.append('text')
          .attr('class', 'collapse-indicator')
          .attr('dy', '0.35em')
          .attr('text-anchor', 'middle')
          .attr('fill', 'var(--bg-primary)')
          .attr('font-size', '10px')
          .attr('font-family', 'Inter, sans-serif')
          .attr('font-weight', '700')
          .attr('pointer-events', 'none')
          .text(d => d._children ? '+' : '');

        // Hover handlers on entering circles
        nodeEnter.select('circle')
          .on('mouseenter', function (event: MouseEvent, d) {
            d3.select(this).attr('stroke-width', 3);
            callbacksRef.current.onNodeHover(event, d.data);
          })
          .on('mouseleave', function (_event, d) {
            d3.select(this).attr('stroke-width', d === selectedNode ? 3 : 2);
            callbacksRef.current.onNodeHoverEnd();
          });

        // Merge enter + update
        const nodeUpdate = nodeEnter.merge(nodeSel);

        // Transition to new positions
        nodeUpdate.transition()
          .duration(duration)
          .attr('transform', d => `translate(${d.y},${d.x})`)
          .style('opacity', 1);

        // Update circle highlights
        nodeUpdate.select('circle')
          .attr('stroke', d => d === selectedNode ? 'var(--text-primary)' : 'var(--bg-primary)')
          .attr('stroke-width', d => d === selectedNode ? 3 : 2);

        // Update collapse indicators
        nodeUpdate.select('.collapse-indicator')
          .text((d) => (d as CollapsibleNode)._children ? '+' : '');

        // Update label anchoring (may change when children collapse/expand)
        nodeUpdate.select('.node-label')
          .attr('x', (d) => ((d as CollapsibleNode).children || (d as CollapsibleNode)._children) ? -LAYOUT.labelOffset : LAYOUT.labelOffset)
          .attr('text-anchor', (d) => ((d as CollapsibleNode).children || (d as CollapsibleNode)._children) ? 'end' : 'start');

        // Exit: transition to source position and remove
        nodeSel.exit()
          .transition()
          .duration(duration)
          .attr('transform', `translate(${source.y},${source.x})`)
          .style('opacity', 0)
          .remove();

        // Apply drag to all current nodes
        nodeUpdate.call(dragBehavior);
      }

      /** Core update function — recalculates layout and delegates to link/node helpers */
      function update(source: CollapsibleNode): void {
        // Resize layout based on visible nodes
        const visibleLeaves = countVisibleLeaves(root);
        const visibleDepth = getVisibleMaxDepth(root);
        const updatedWidth = Math.max(LAYOUT.minWidth, (visibleDepth + 1) * LAYOUT.levelWidth + 100);
        const updatedHeight = Math.max(LAYOUT.minHeight, visibleLeaves * LAYOUT.nodeSpacing + 60);

        treeLayout.size([updatedHeight - margin.top - margin.bottom, updatedWidth - margin.left - margin.right]);

        // Update viewBox
        d3.select(svgEl)
          .attr('viewBox', `0 0 ${updatedWidth} ${updatedHeight}`);

        // Recalculate layout
        treeLayout(root);
        const nodes = root.descendants() as CollapsibleNode[];
        const links = root.links() as d3.HierarchyPointLink<OpeningNode>[];

        const duration = LAYOUT.transitionDuration;

        updateLinks(source, links, duration);
        updateNodes(source, nodes, duration);
      }

      // Initial render
      update(root);

      // Click on SVG background to deselect
      d3.select(svgEl).on('click.deselect', function (event: MouseEvent) {
        if (event.target === svgEl || event.target === gEl) {
          selectedNode = null;
          update(root);
        }
      });

      // Zoom/pan behavior on SVG
      const zoomBehavior = d3.zoom<SVGSVGElement, unknown>()
        .scaleExtent(LAYOUT.zoomExtent)
        .on('start', () => {
          d3.select(svgEl).style('cursor', 'grabbing');
        })
        .on('zoom', (event: d3.D3ZoomEvent<SVGSVGElement, unknown>) => {
          gSelection.attr('transform', event.transform.toString());
        })
        .on('end', () => {
          d3.select(svgEl).style('cursor', 'grab');
        });

      zoomBehaviorRef.current = zoomBehavior;
      d3.select(svgEl).call(zoomBehavior);
    } catch (e) {
      callbacksRef.current.onError?.(
        e instanceof Error ? e.message : 'Failed to draw opening tree.'
      );
    }

    // Cleanup
    return () => {
      if (svgEl) {
        d3.select(svgEl).on('.zoom', null);
        d3.select(svgEl).on('click.deselect', null);
      }
      if (gEl) {
        d3.select(gEl).selectAll('*').remove();
      }
      zoomBehaviorRef.current = null;
    };
  }, [data]);

  return (
    <svg
      ref={svgRef}
      width="100%"
      height="100%"
      className="text-primary block mx-auto"
      style={{ cursor: 'grab' }}
    >
      <g ref={gRef} />
    </svg>
  );
}
