import { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import * as d3 from 'd3';
import { getOpenings, type OpeningNode } from '../api/client';

export default function Openings() {
  const svgRef = useRef<SVGSVGElement>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const fetchAndRender = async () => {
      try {
        const data = await getOpenings();
        renderTree(data);
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Failed to load openings');
      } finally {
        setLoading(false);
      }
    };

    fetchAndRender();
  }, []);

  const renderTree = (data: OpeningNode) => {
    if (!svgRef.current) return;

    const width = 800;
    const height = 500;
    const margin = { top: 20, right: 120, bottom: 20, left: 120 };

    // Clear previous content
    d3.select(svgRef.current).selectAll('*').remove();

    const svg = d3.select(svgRef.current)
      .attr('width', width)
      .attr('height', height)
      .append('g')
      .attr('transform', `translate(${margin.left},${margin.top})`);

    const root = d3.hierarchy(data);
    const treeLayout = d3.tree<OpeningNode>().size([
      height - margin.top - margin.bottom,
      width - margin.left - margin.right - 100
    ]);

    const treeData = treeLayout(root);

    // Links
    svg.selectAll('.link')
      .data(treeData.links())
      .enter()
      .append('path')
      .attr('class', 'link')
      .attr('fill', 'none')
      .attr('stroke', '#4b5563')
      .attr('stroke-width', 2)
      .attr('d', d3.linkHorizontal<d3.HierarchyPointLink<OpeningNode>, d3.HierarchyPointNode<OpeningNode>>()
        .x(d => d.y)
        .y(d => d.x)
      );

    // Nodes
    const nodes = svg.selectAll('.node')
      .data(treeData.descendants())
      .enter()
      .append('g')
      .attr('class', 'node')
      .attr('transform', d => `translate(${d.y},${d.x})`);

    nodes.append('circle')
      .attr('r', 8)
      .attr('fill', '#10b981')
      .attr('stroke', '#059669')
      .attr('stroke-width', 2);

    nodes.append('text')
      .attr('dy', 4)
      .attr('x', d => d.children ? -12 : 12)
      .attr('text-anchor', d => d.children ? 'end' : 'start')
      .attr('fill', '#e5e7eb')
      .attr('font-size', '12px')
      .text(d => `${d.data.name} (${d.data.count})`);
  };

  return (
    <div className="min-h-screen bg-gray-900 text-white">
      <nav className="bg-gray-800 p-4">
        <div className="container mx-auto flex gap-6">
          <Link to="/" className="text-xl font-bold text-emerald-400">KnightMind</Link>
          <Link to="/openings" className="hover:text-emerald-400 text-emerald-400">Openings</Link>
        </div>
      </nav>

      <main className="container mx-auto p-8">
        <h1 className="text-4xl font-bold mb-8">Opening Tree</h1>
        <p className="text-gray-400 mb-8">Visualize your most played openings</p>

        <div className="bg-gray-800 rounded-lg p-6 overflow-x-auto">
          {loading && <p className="text-gray-400">Loading openings...</p>}
          {error && <p className="text-red-400">{error}</p>}
          <svg ref={svgRef}></svg>
        </div>
      </main>
    </div>
  );
}
