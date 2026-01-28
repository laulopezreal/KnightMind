import { useEffect, useRef, useState, useCallback } from 'react';
import { Link } from 'react-router-dom';
import * as d3 from 'd3';
import { getOpenings, ApiError, type OpeningNode, type ColorFilter } from '../api/client';

export default function Openings() {
  const svgRef = useRef<SVGSVGElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [username, setUsername] = useState('');
  const [colorFilter, setColorFilter] = useState<ColorFilter>('both');
  const [treeData, setTreeData] = useState<OpeningNode | null>(null);
  const [tooltip, setTooltip] = useState<{ x: number; y: number; data: OpeningNode } | null>(null);

  const fetchOpenings = useCallback(async (user: string, color: ColorFilter) => {
    if (!user.trim()) {
      setError('Please enter a username');
      return;
    }
    
    setLoading(true);
    setError(null);
    
    try {
      const data = await getOpenings(user, color);
      setTreeData(data);
    } catch (err) {
      if (err instanceof ApiError) {
        setError(err.detail || err.message);
      } else {
        setError(err instanceof Error ? err.message : 'Failed to load openings');
      }
      setTreeData(null);
    } finally {
      setLoading(false);
    }
  }, []);

  const handleFetchClick = () => {
    fetchOpenings(username, colorFilter);
  };

  // Re-render tree when data changes
  useEffect(() => {
    if (treeData && svgRef.current) {
      renderTree(treeData);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [treeData]);

  // Refetch when color filter changes (if we have data)
  useEffect(() => {
    if (username.trim() && treeData) {
      fetchOpenings(username, colorFilter);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [colorFilter]);

  const renderTree = (data: OpeningNode) => {
    if (!svgRef.current) return;

    // Calculate dynamic dimensions based on tree size
    const leafCount = countLeaves(data);
    const maxDepth = getMaxDepth(data);
    const nodeSpacing = 35; // Vertical spacing between nodes
    const levelWidth = 180; // Horizontal spacing between levels
    
    const width = Math.max(800, (maxDepth + 1) * levelWidth + 100);
    const height = Math.max(400, leafCount * nodeSpacing + 60);
    const margin = { top: 30, right: 150, bottom: 30, left: 60 };

    // Clear previous content
    d3.select(svgRef.current).selectAll('*').remove();

    const svg = d3.select(svgRef.current)
      .attr('width', width)
      .attr('height', height);

    const g = svg.append('g')
      .attr('transform', `translate(${margin.left},${margin.top})`);

    const root = d3.hierarchy(data);
    const treeLayout = d3.tree<OpeningNode>()
      .size([height - margin.top - margin.bottom, width - margin.left - margin.right])
      .separation((a, b) => (a.parent === b.parent ? 1 : 1.2));

    const treeDataLayout = treeLayout(root);

    // Links with curved paths
    g.selectAll('.link')
      .data(treeDataLayout.links())
      .enter()
      .append('path')
      .attr('class', 'link')
      .attr('fill', 'none')
      .attr('stroke', '#4b5563')
      .attr('stroke-width', 2)
      .attr('stroke-opacity', 0.6)
      .attr('d', d3.linkHorizontal<d3.HierarchyPointLink<OpeningNode>, d3.HierarchyPointNode<OpeningNode>>()
        .x(d => d.y)
        .y(d => d.x)
      );

    // Nodes
    const nodes = g.selectAll('.node')
      .data(treeDataLayout.descendants())
      .enter()
      .append('g')
      .attr('class', 'node')
      .attr('transform', d => `translate(${d.y},${d.x})`)
      .style('cursor', 'pointer');

    // Node circles - size based on game count
    nodes.append('circle')
      .attr('r', d => Math.max(6, Math.min(16, Math.sqrt(d.data.games_count) * 1.5 + 4)))
      .attr('fill', d => getWinRateColor(d.data.win_rate))
      .attr('stroke', '#fff')
      .attr('stroke-width', 2)
      .on('mouseenter', function(event, d) {
        d3.select(this).attr('stroke-width', 3);
        const rect = containerRef.current?.getBoundingClientRect();
        if (rect) {
          setTooltip({
            x: event.clientX - rect.left + 10,
            y: event.clientY - rect.top - 10,
            data: d.data
          });
        }
      })
      .on('mouseleave', function() {
        d3.select(this).attr('stroke-width', 2);
        setTooltip(null);
      });

    // Move labels - show only the move, not all stats
    nodes.append('text')
      .attr('dy', 5)
      .attr('x', d => d.children ? -20 : 20)
      .attr('text-anchor', d => d.children ? 'end' : 'start')
      .attr('fill', '#f3f4f6')
      .attr('font-size', '13px')
      .attr('font-weight', '500')
      .text(d => d.data.move_san === 'Start' ? '●' : d.data.move_san);

    // Game count badge
    nodes.append('text')
      .attr('dy', -12)
      .attr('text-anchor', 'middle')
      .attr('fill', '#9ca3af')
      .attr('font-size', '10px')
      .text(d => d.data.games_count > 1 ? d.data.games_count.toString() : '');
  };

  const countLeaves = (node: OpeningNode): number => {
    if (!node.children || node.children.length === 0) return 1;
    return node.children.reduce((sum, child) => sum + countLeaves(child), 0);
  };

  const getMaxDepth = (node: OpeningNode, depth = 0): number => {
    if (!node.children || node.children.length === 0) return depth;
    return Math.max(...node.children.map(child => getMaxDepth(child, depth + 1)));
  };

  const getWinRateColor = (winRate: number): string => {
    // Green for high win rate, red for low, yellow for ~50%
    if (winRate >= 60) return '#10b981'; // Emerald
    if (winRate >= 50) return '#22c55e'; // Green
    if (winRate >= 45) return '#84cc16'; // Lime
    if (winRate >= 40) return '#eab308'; // Yellow
    if (winRate >= 30) return '#f97316'; // Orange
    return '#ef4444'; // Red
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
        <h1 className="text-4xl font-bold mb-4">Opening Tree</h1>
        <p className="text-gray-400 mb-6">Visualize your most played openings with win/loss statistics</p>

        {/* Controls */}
        <div className="bg-gray-800 rounded-lg p-4 mb-6">
          <div className="flex flex-wrap gap-4 items-end">
            <div>
              <label className="block text-sm text-gray-400 mb-1">Username</label>
              <input
                type="text"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && handleFetchClick()}
                placeholder="Chess.com username"
                className="px-4 py-2 rounded bg-gray-700 border border-gray-600 focus:border-emerald-400 focus:outline-none"
              />
            </div>
            <div>
              <label className="block text-sm text-gray-400 mb-1">Color</label>
              <select
                value={colorFilter}
                onChange={(e) => setColorFilter(e.target.value as ColorFilter)}
                className="px-4 py-2 rounded bg-gray-700 border border-gray-600 focus:border-emerald-400 focus:outline-none"
              >
                <option value="both">Both</option>
                <option value="white">White</option>
                <option value="black">Black</option>
              </select>
            </div>
            <button
              onClick={handleFetchClick}
              disabled={loading}
              className="px-6 py-2 bg-emerald-600 hover:bg-emerald-700 disabled:bg-gray-600 rounded font-medium transition-colors"
            >
              {loading ? 'Loading...' : 'Load Openings'}
            </button>
          </div>
          {error && <p className="text-red-400 mt-3 text-sm">{error}</p>}
        </div>

        {/* Legend */}
        {treeData && (
          <div className="flex flex-wrap gap-4 mb-4 text-sm">
            <span className="flex items-center gap-1">
              <span className="w-3 h-3 rounded-full bg-emerald-500"></span> &ge;60%
            </span>
            <span className="flex items-center gap-1">
              <span className="w-3 h-3 rounded-full bg-green-500"></span> 50-60%
            </span>
            <span className="flex items-center gap-1">
              <span className="w-3 h-3 rounded-full bg-lime-500"></span> 45-50%
            </span>
            <span className="flex items-center gap-1">
              <span className="w-3 h-3 rounded-full bg-yellow-500"></span> 40-45%
            </span>
            <span className="flex items-center gap-1">
              <span className="w-3 h-3 rounded-full bg-orange-500"></span> 30-40%
            </span>
            <span className="flex items-center gap-1">
              <span className="w-3 h-3 rounded-full bg-red-500"></span> &lt;30%
            </span>
          </div>
        )}

        {/* Tree visualization */}
        <div ref={containerRef} className="bg-gray-800 rounded-lg p-6 overflow-x-auto relative">
          {!treeData && !loading && (
            <p className="text-gray-400">Enter a username and click "Load Openings" to visualize your opening repertoire.</p>
          )}
          {loading && <p className="text-gray-400">Building opening tree...</p>}
          <svg ref={svgRef}></svg>
          
          {/* Tooltip */}
          {tooltip && (
            <div 
              className="absolute bg-gray-900 border border-gray-600 rounded-lg p-3 shadow-xl z-10 pointer-events-none"
              style={{ left: tooltip.x, top: tooltip.y }}
            >
              <div className="font-bold text-white mb-1">
                {tooltip.data.move_san === 'Start' ? 'Starting Position' : tooltip.data.move_san}
              </div>
              <div className="text-sm text-gray-300 space-y-1">
                <div>Games: <span className="text-white font-medium">{tooltip.data.games_count}</span></div>
                <div className="flex gap-3">
                  <span className="text-green-400">W: {tooltip.data.wins}</span>
                  <span className="text-gray-400">D: {tooltip.data.draws}</span>
                  <span className="text-red-400">L: {tooltip.data.losses}</span>
                </div>
                <div>Win rate: <span className="font-medium" style={{ color: getWinRateColor(tooltip.data.win_rate) }}>{tooltip.data.win_rate}%</span></div>
              </div>
            </div>
          )}
        </div>

        {/* Stats summary */}
        {treeData && (
          <div className="mt-4 text-sm text-gray-400">
            Total games: {treeData.games_count} | 
            Wins: {treeData.wins} | 
            Draws: {treeData.draws} | 
            Losses: {treeData.losses} | 
            Win rate: {treeData.win_rate}%
          </div>
        )}
      </main>
    </div>
  );
}
