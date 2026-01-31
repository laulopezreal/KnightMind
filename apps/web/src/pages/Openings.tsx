import { useEffect, useRef, useState, useCallback } from 'react';
import * as d3 from 'd3';
import { getOpenings, ApiError, type OpeningNode, type ColorFilter } from '../api/client';
import { useChessUsername } from '../context/ChessUsernameContext';

export default function Openings() {
  const svgRef = useRef<SVGSVGElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const { username, setEditorOpen } = useChessUsername();
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

  useEffect(() => {
    if (treeData && svgRef.current) {
      renderTree(treeData);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [treeData]);

  useEffect(() => {
    if (username.trim() && treeData) {
      fetchOpenings(username, colorFilter);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [colorFilter]);

  const renderTree = (data: OpeningNode) => {
    if (!svgRef.current) return;
    const leafCount = countLeaves(data);
    const maxDepth = getMaxDepth(data);
    const nodeSpacing = 35;
    const levelWidth = 180;

    const width = Math.max(800, (maxDepth + 1) * levelWidth + 100);
    const height = Math.max(400, leafCount * nodeSpacing + 60);
    const margin = { top: 30, right: 150, bottom: 30, left: 60 };

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

    // Links
    g.selectAll('.link')
      .data(treeDataLayout.links())
      .enter()
      .append('path')
      .attr('class', 'link')
      .attr('fill', 'none')
      .attr('stroke', 'currentColor') // Use current text color
      .attr('stroke-width', 1.5)
      .attr('stroke-opacity', 0.2)
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

    nodes.append('circle')
      .attr('r', d => Math.max(6, Math.min(16, Math.sqrt(d.data.games_count) * 1.5 + 4)))
      .attr('fill', d => getWinRateColor(d.data.win_rate))
      .attr('stroke', 'var(--bg-primary)')
      .attr('stroke-width', 2)
      .on('mouseenter', function (event, d) {
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
      .on('mouseleave', function () {
        d3.select(this).attr('stroke-width', 2);
        setTooltip(null);
      });

    // Labels
    nodes.append('text')
      .attr('dy', 5)
      .attr('x', d => d.children ? -20 : 20)
      .attr('text-anchor', d => d.children ? 'end' : 'start')
      .attr('fill', 'currentColor')
      .attr('font-size', '13px')
      .attr('font-family', 'Inter, sans-serif')
      .attr('font-weight', '500')
      .text(d => d.data.move_san === 'Start' ? '●' : d.data.move_san);
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
    // Elegant Muted Palette for stats
    if (winRate >= 60) return '#059669'; // Emerald 600
    if (winRate >= 50) return '#10B981'; // Emerald 500
    if (winRate >= 45) return '#84CC16'; // Lime 500
    if (winRate >= 40) return '#EAB308'; // Yellow 500
    if (winRate >= 30) return '#F97316'; // Orange 500
    return '#EF4444'; // Red 500
  };

  return (
    <div className="space-y-12 animate-teedin">
      <section>
        <h1 className="text-4xl md:text-5xl font-serif text-primary mb-4">Opening Tree</h1>
        <p className="text-lg text-primary/60 font-sans max-w-2xl">
          Visualize your repertoire. Discover where you win, where you lose, and where you can improve.
        </p>
      </section>

      {/* Controls */}
      <section className="flex flex-wrap gap-6 items-end p-6 border border-primary/10 rounded-lg bg-primary/5 backdrop-blur-sm">
        <div className="flex-1 min-w-[200px]">
          {!username ? (
            <div className="h-full flex flex-col justify-center">
              <div className="flex items-center gap-2">
                <span className="text-primary/60 font-sans text-sm">Set username to analyze</span>
                <button
                  onClick={() => setEditorOpen(true)}
                  className="text-primary underline hover:text-primary/80 text-sm font-medium"
                >
                  Set
                </button>
              </div>
            </div>
          ) : (
            <div>
              <label className="block text-xs font-sans uppercase tracking-widest text-primary/40 mb-2">Username</label>
              <div className="font-serif text-xl text-primary border-b border-primary/20 py-2">
                {username}
              </div>
            </div>
          )}
        </div>

        <div className="w-40">
          <label className="block text-xs font-sans uppercase tracking-widest text-primary/40 mb-2">Color</label>
          <select
            value={colorFilter}
            onChange={(e) => setColorFilter(e.target.value as ColorFilter)}
            className="w-full bg-transparent border-b border-primary/20 py-2 text-primary focus:outline-none focus:border-primary/60 transition-colors font-serif text-xl cursor-pointer"
          >
            <option value="both">Both</option>
            <option value="white">White</option>
            <option value="black">Black</option>
          </select>
        </div>

        <button
          onClick={handleFetchClick}
          disabled={loading || !username}
          className="px-8 py-3 bg-primary text-bg-primary hover:opacity-90 disabled:opacity-50 rounded-sm font-serif text-lg transition-all"
        >
          {loading ? 'Analyzing...' : 'Load Openings'}
        </button>
      </section>

      {/* Legend */}
      <section className="flex gap-4 items-center justify-center text-xs font-sans text-primary/60">
        <span className="uppercase tracking-widest mr-2">Win Rate:</span>
        <div className="flex items-center gap-1"><div className="w-3 h-3 rounded-full bg-emerald-600"></div> 60%+</div>
        <div className="flex items-center gap-1"><div className="w-3 h-3 rounded-full bg-emerald-500"></div> 50%+</div>
        <div className="flex items-center gap-1"><div className="w-3 h-3 rounded-full bg-lime-500"></div> 45%+</div>
        <div className="flex items-center gap-1"><div className="w-3 h-3 rounded-full bg-yellow-500"></div> 40%+</div>
        <div className="flex items-center gap-1"><div className="w-3 h-3 rounded-full bg-orange-500"></div> 30%+</div>
        <div className="flex items-center gap-1"><div className="w-3 h-3 rounded-full bg-red-500"></div> &lt;30%</div>
      </section>

      {error && <p className="text-red-500/80 font-sans">{error}</p>}

      {/* Visualization */}
      <section ref={containerRef} className="relative overflow-hidden min-h-[500px] border-t border-primary/10 pt-8">
        {!treeData && !loading && (
          <div className="absolute inset-0 flex items-center justify-center opacity-20 pointer-events-none">
            <span className="text-9xl font-serif">♔</span>
          </div>
        )}

        {loading && (
          <div className="absolute inset-0 flex items-center justify-center">
            <p className="font-serif text-xl animate-pulse text-primary/60">Tracing paths...</p>
          </div>
        )}

        <div className="overflow-x-auto">
          <svg ref={svgRef} className="text-primary block mx-auto"></svg>
        </div>

        {/* Custom Tooltip */}
        {tooltip && (
          <div
            className="absolute z-50 bg-bg-primary border border-primary/20 p-4 shadow-2xl rounded-sm pointer-events-none min-w-[200px]"
            style={{ left: tooltip.x, top: tooltip.y }}
          >
            <div className="font-serif text-xl text-primary mb-2 border-b border-primary/10 pb-2">
              {tooltip.data.move_san === 'Start' ? 'Start' : tooltip.data.move_san}
            </div>
            <div className="space-y-1 font-sans text-sm text-primary/80">
              <div className="flex justify-between"><span>Games</span> <span>{tooltip.data.games_count}</span></div>
              <div className="flex justify-between text-green-600"><span>Won</span> <span>{tooltip.data.wins}</span></div>
              <div className="flex justify-between text-gray-500"><span>Draw</span> <span>{tooltip.data.draws}</span></div>
              <div className="flex justify-between text-red-500"><span>Lost</span> <span>{tooltip.data.losses}</span></div>
              <div className="pt-2 border-t border-primary/10 flex justify-between font-medium">
                <span>Win Rate</span>
                <span style={{ color: getWinRateColor(tooltip.data.win_rate) }}>{tooltip.data.win_rate}%</span>
              </div>
            </div>
          </div>
        )}
      </section>
    </div >
  );
}
