import { useEffect, useRef } from 'react';
import * as d3 from 'd3';
import type { OpeningNode } from '../api';
import { getWinRateColor } from '../utils/openings';

function countLeaves(node: OpeningNode): number {
  if (!node.children || node.children.length === 0) return 1;
  return node.children.reduce((sum, child) => sum + countLeaves(child), 0);
}

function getMaxDepth(node: OpeningNode, depth = 0): number {
  if (!node.children || node.children.length === 0) return depth;
  return Math.max(...node.children.map((child) => getMaxDepth(child, depth + 1)));
}

interface OpeningGraphProps {
  data: OpeningNode;
  onNodeHover: (event: MouseEvent, node: OpeningNode) => void;
  onNodeHoverEnd: () => void;
}

export function OpeningGraph({ data, onNodeHover, onNodeHoverEnd }: OpeningGraphProps) {
  const svgRef = useRef<SVGSVGElement>(null);
  const gRef = useRef<SVGGElement>(null);
  const callbacksRef = useRef({ onNodeHover, onNodeHoverEnd });
  callbacksRef.current = { onNodeHover, onNodeHoverEnd };

  useEffect(() => {
    const svgEl = svgRef.current;
    const gEl = gRef.current;
    if (!svgEl || !gEl) return;

    // Clear previous D3 content
    const gSelection = d3.select(gEl);
    gSelection.selectAll('*').remove();

    // Calculate dimensions from tree shape
    const leafCount = countLeaves(data);
    const maxDepth = getMaxDepth(data);
    const nodeSpacing = 35;
    const levelWidth = 180;

    const width = Math.max(800, (maxDepth + 1) * levelWidth + 100);
    const height = Math.max(400, leafCount * nodeSpacing + 60);
    const margin = { top: 30, right: 150, bottom: 30, left: 60 };

    // Configure SVG with viewBox for zoom/pan support
    d3.select(svgEl)
      .attr('viewBox', `0 0 ${width} ${height}`)
      .attr('preserveAspectRatio', 'xMidYMid meet');

    // Content group offset by margins
    const contentG = gSelection.append('g')
      .attr('transform', `translate(${margin.left},${margin.top})`);

    // Build hierarchy and tree layout
    const root = d3.hierarchy(data);
    const treeLayout = d3.tree<OpeningNode>()
      .size([height - margin.top - margin.bottom, width - margin.left - margin.right])
      .separation((a, b) => (a.parent === b.parent ? 1 : 1.2));

    const treeDataLayout = treeLayout(root);

    // Link path generator (reused during drag updates)
    const linkPathGen = d3.linkHorizontal<
      d3.HierarchyPointLink<OpeningNode>,
      d3.HierarchyPointNode<OpeningNode>
    >()
      .x(d => d.y)
      .y(d => d.x);

    // Render links
    contentG.append('g')
      .attr('class', 'links')
      .selectAll('path')
      .data(treeDataLayout.links())
      .enter()
      .append('path')
      .attr('class', 'link')
      .attr('fill', 'none')
      .attr('stroke', 'currentColor')
      .attr('stroke-width', 1.5)
      .attr('stroke-opacity', 0.2)
      .attr('d', linkPathGen);

    // Render node groups
    const nodeGroups = contentG.append('g')
      .attr('class', 'nodes')
      .selectAll('g')
      .data(treeDataLayout.descendants())
      .enter()
      .append('g')
      .attr('class', 'node')
      .attr('transform', d => `translate(${d.y},${d.x})`)
      .style('cursor', 'pointer');

    // Node circles
    nodeGroups.append('circle')
      .attr('r', d => Math.max(6, Math.min(16, Math.sqrt(d.data.games_count) * 1.5 + 4)))
      .attr('fill', d => getWinRateColor(d.data.win_rate))
      .attr('stroke', 'var(--bg-primary)')
      .attr('stroke-width', 2)
      .on('mouseenter', function (event: MouseEvent, d) {
        d3.select(this).attr('stroke-width', 3);
        callbacksRef.current.onNodeHover(event, d.data);
      })
      .on('mouseleave', function () {
        d3.select(this).attr('stroke-width', 2);
        callbacksRef.current.onNodeHoverEnd();
      });

    // Node labels
    nodeGroups.append('text')
      .attr('dy', 5)
      .attr('x', d => d.children ? -20 : 20)
      .attr('text-anchor', d => d.children ? 'end' : 'start')
      .attr('fill', 'currentColor')
      .attr('font-size', '13px')
      .attr('font-family', 'Inter, sans-serif')
      .attr('font-weight', '500')
      .text(d => d.data.move_san === 'Start' ? '●' : d.data.move_san);

    // Drag behavior on node groups
    const contentNode = contentG.node();
    const dragBehavior = d3.drag<SVGGElement, d3.HierarchyPointNode<OpeningNode>>()
      .container(function () { return contentNode as SVGGElement; })
      .on('start', function (event) {
        event.sourceEvent.stopPropagation();
        d3.select(this).raise();
        d3.select(svgEl).style('cursor', 'grabbing');
        d3.select(this).style('cursor', 'grabbing');
      })
      .on('drag', function (event, d) {
        d.y = event.x;
        d.x = event.y;
        d3.select(this).attr('transform', `translate(${d.y},${d.x})`);
        // Redraw all links with updated positions
        contentG.select('g.links')
          .selectAll<SVGPathElement, d3.HierarchyPointLink<OpeningNode>>('path')
          .attr('d', linkPathGen);
      })
      .on('end', function () {
        d3.select(svgEl).style('cursor', 'grab');
        d3.select(this).style('cursor', 'pointer');
      });

    nodeGroups.call(dragBehavior);

    // Zoom/pan behavior on SVG
    const zoomBehavior = d3.zoom<SVGSVGElement, unknown>()
      .scaleExtent([0.3, 3])
      .on('start', () => {
        d3.select(svgEl).style('cursor', 'grabbing');
      })
      .on('zoom', (event: d3.D3ZoomEvent<SVGSVGElement, unknown>) => {
        gSelection.attr('transform', event.transform.toString());
      })
      .on('end', () => {
        d3.select(svgEl).style('cursor', 'grab');
      });

    d3.select(svgEl).call(zoomBehavior);

    // Cleanup
    return () => {
      if (svgEl) {
        d3.select(svgEl).on('.zoom', null);
      }
      if (gEl) {
        d3.select(gEl).selectAll('*').remove();
      }
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
