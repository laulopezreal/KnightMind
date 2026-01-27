import { useEffect, useRef, useState, useCallback } from 'react';
import { Link } from 'react-router-dom';
import * as d3 from 'd3';
import { getOpenings, ApiError, type OpeningNode, type ColorFilter } from '../api/client';

export default function Openings() {
  const svgRef = useRef<SVGSVGElement>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [username, setUsername] = useState('');
  const [colorFilter, setColorFilter] = useState<ColorFilter>('both');
  const [treeData, setTreeData] = useState<OpeningNode | null>(null);

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
    // renderTree is stable (defined inside component but doesn't depend on any state)
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

    // Calculate dynamic height based on tree size
    const nodeCount = countNodes(data);
    const width = 900;
    const height = Math.max(500, nodeCount * 25);
    const margin = { top: 20, right: 200, bottom: 20, left: 80 };

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
      width - margin.left - margin.right
    ]);

    const treeDataLayout = treeLayout(root);

    // Links
    svg.selectAll('.link')
      .data(treeDataLayout.links())
      .enter()
      .append('path')
      .attr('class', 'link')
      .attr('fill', 'none')
      .attr('stroke', '#4b5563')
      .attr('stroke-width', 1.5)
      .attr('d', d3.linkHorizontal<d3.HierarchyPointLink<OpeningNode>, d3.HierarchyPointNode<OpeningNode>>()
        .x(d => d.y)
        .y(d => d.x)
      );

    // Nodes
    const nodes = svg.selectAll('.node')
      .data(treeDataLayout.descendants())
      .enter()
      .append('g')
      .attr('class', 'node')
      .attr('transform', d => `translate(${d.y},${d.x})`);

    // Color based on win rate
    nodes.append('circle')
      .attr('r', d => Math.max(4, Math.min(12, Math.sqrt(d.data.games_count) * 2)))
      .attr('fill', d => getWinRateColor(d.data.win_rate))
      .attr('stroke', '#1f2937')
      .attr('stroke-width', 1);

    // Labels
    nodes.append('text')
      .attr('dy', 4)
      .attr('x', d => d.children ? -14 : 14)
      .attr('text-anchor', d => d.children ? 'end' : 'start')
      .attr('fill', '#e5e7eb')
      .attr('font-size', '11px')
      .text(d => {
        const move = d.data.move_san === 'Start' ? 'Start' : d.data.move_san;
        return `${move} (${d.data.games_count}) ${d.data.win_rate}%`;
      });
  };

  const countNodes = (node: OpeningNode): number => {
    let count = 1;
    if (node.children) {
      for (const child of node.children) {
        count += countNodes(child);
      }
    }
    return count;
  };

  const getWinRateColor = (winRate: number): string => {
    // Green for high win rate, red for low, yellow for ~50%
    if (winRate >= 60) return '#10b981'; // Green
    if (winRate >= 50) return '#84cc16'; // Lime
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
          <div className="flex gap-4 mb-4 text-sm">
            <span className="flex items-center gap-1">
              <span className="w-3 h-3 rounded-full bg-emerald-500"></span> &gt;60% win
            </span>
            <span className="flex items-center gap-1">
              <span className="w-3 h-3 rounded-full bg-lime-500"></span> 50-60%
            </span>
            <span className="flex items-center gap-1">
              <span className="w-3 h-3 rounded-full bg-yellow-500"></span> 40-50%
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
        <div className="bg-gray-800 rounded-lg p-6 overflow-x-auto">
          {!treeData && !loading && (
            <p className="text-gray-400">Enter a username and click "Load Openings" to visualize your opening repertoire.</p>
          )}
          {loading && <p className="text-gray-400">Building opening tree...</p>}
          <svg ref={svgRef}></svg>
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
