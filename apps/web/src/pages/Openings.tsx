import { useEffect, useRef, useState, useCallback } from 'react';
import { createPortal } from 'react-dom';
import { getOpenings, ApiError, type OpeningNode, type ColorFilter } from '../api';
import { useChessUsername } from '../context/ChessUsernameContext';
import { OpeningGraph } from '../components/OpeningGraph';
import { getWinRateColor } from '../utils/openings';
import { useLocalStorage } from '../hooks/useLocalStorage';

function countAllNodes(node: OpeningNode): number {
  let count = 1;
  if (node.children) {
    for (const child of node.children) count += countAllNodes(child);
  }
  return count;
}

export default function Openings() {
  const containerRef = useRef<HTMLDivElement>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const { username, setEditorOpen } = useChessUsername();
  const [colorFilter, setColorFilter] = useLocalStorage<ColorFilter>(
    'knightmind:openings:color_filter',
    'both'
  );
  const [treeData, setTreeData] = useState<OpeningNode | null>(null);
  const [tooltip, setTooltip] = useState<{ x: number; y: number; data: OpeningNode } | null>(null);

  const subtitle = (() => {
    if (!username) {
      return 'Set your Chess.com username to explore your opening repertoire.';
    }
    if (loading) {
      return 'Building your opening tree...';
    }
    if (treeData) {
      return 'Your repertoire\u2009—\u2009scroll to zoom, drag to pan, click nodes to explore.';
    }
    return 'Load your games to build your opening knowledge graph.';
  })();

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

  // Auto-fetch when page loads with username or when username/color filter changes
  useEffect(() => {
    if (username.trim()) {
      fetchOpenings(username, colorFilter);
    }
  }, [username, colorFilter, fetchOpenings]);

  return (
    <div className="space-y-8 animate-teedin">
      {/* Header */}
      <section>
        <h1 className="text-4xl md:text-5xl font-serif text-primary mb-2">Opening Explorer</h1>
        <p className="text-lg text-primary/60 font-sans max-w-2xl">{subtitle}</p>
      </section>

      {/* Controls */}
      <section className="flex flex-wrap gap-4 items-end p-6 border border-primary/10 rounded-sm bg-primary/5 backdrop-blur-sm">
        <div className="flex-1 min-w-[200px]">
          {!username ? (
            <div className="h-full flex flex-col justify-center">
              <div className="flex items-center gap-2">
                <span className="text-primary/60 font-sans text-sm">Set username to analyze</span>
                <button
                  type="button"
                  onClick={() => setEditorOpen(true)}
                  className="km-interactive km-focus-visible km-inline-link text-primary text-sm font-medium"
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
          type="button"
          onClick={handleFetchClick}
          disabled={loading || !username}
          className={[
            'px-6 py-2 rounded-sm font-serif transition-all km-focus-visible',
            loading || !username ? 'km-interactive-disabled disabled:opacity-50' : 'km-interactive',
            treeData
              ? 'border border-primary/20 text-primary hover:bg-primary hover:text-bg-primary'
              : 'bg-primary text-bg-primary',
          ].join(' ')}
        >
          {treeData
            ? loading ? 'Refreshing...' : 'Refresh'
            : loading ? 'Analyzing...' : 'Load Openings'}
        </button>
      </section>

      {/* Error */}
      {error && (
        <div className="flex items-center gap-4">
          <p className="text-red-500/80 font-sans">{error}</p>
          <button
            type="button"
            onClick={handleFetchClick}
            className="px-4 py-1 border border-red-500/20 text-red-500/80 hover:bg-red-500/10 rounded-sm font-serif transition-colors km-interactive km-focus-visible text-sm"
          >
            Retry
          </button>
        </div>
      )}

      {/* Graph */}
      <section ref={containerRef} className="relative min-h-[300px] md:min-h-[500px] max-h-[70vh] bg-primary/5 border border-primary/10 rounded-sm overflow-hidden">
        {!treeData && !loading && !error && (
          <div className="absolute inset-0 flex flex-col items-center justify-center">
            <span className="text-9xl font-serif text-primary/10">♔</span>
            <p className="mt-4 text-primary/30 font-sans text-sm">
              {username ? 'Load your openings to see your repertoire' : 'Connect your account to get started'}
            </p>
          </div>
        )}

        {loading && (
          <div className="absolute inset-0 flex flex-col items-center justify-center">
            <p className="font-serif text-xl animate-pulse text-primary/60">Tracing paths...</p>
          </div>
        )}

        {treeData && !loading && (
          <OpeningGraph
            data={treeData}
            onNodeHover={(event, node) => setTooltip({
              x: event.clientX + 10,
              y: event.clientY - 10,
              data: node,
            })}
            onNodeHoverEnd={() => setTooltip(null)}
            onError={setError}
          />
        )}

        {treeData && !loading && (
          <div className="absolute bottom-3 left-3 text-xs text-primary/30 font-sans bg-bg-primary/80 px-2 py-1 rounded-sm pointer-events-none">
            Scroll to zoom · Drag to pan
          </div>
        )}

        {/* Tooltip in portal so it is not clipped by overflow */}
        {tooltip &&
          createPortal(
            <div
              className="fixed z-[9999] bg-bg-primary border border-primary/20 p-4 shadow-2xl rounded-sm pointer-events-none min-w-[200px]"
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
            </div>,
            document.body
          )}
      </section>

      {/* Legend + Stats */}
      <section className="flex flex-wrap gap-6 items-center justify-center text-xs font-sans text-primary/60">
        <span className="uppercase tracking-widest mr-2">Win Rate:</span>
        <div className="flex items-center gap-1"><div className="w-3 h-3 rounded-full bg-emerald-600"></div> 60%+</div>
        <div className="flex items-center gap-1"><div className="w-3 h-3 rounded-full bg-emerald-500"></div> 50%+</div>
        <div className="flex items-center gap-1"><div className="w-3 h-3 rounded-full bg-lime-500"></div> 45%+</div>
        <div className="flex items-center gap-1"><div className="w-3 h-3 rounded-full bg-yellow-500"></div> 40%+</div>
        <div className="flex items-center gap-1"><div className="w-3 h-3 rounded-full bg-orange-500"></div> 30%+</div>
        <div className="flex items-center gap-1"><div className="w-3 h-3 rounded-full bg-red-500"></div> &lt;30%</div>

        {treeData && (
          <>
            <span className="border-l border-primary/20 pl-6 ml-2">
              <span className="font-mono">{treeData.games_count}</span> games
            </span>
            <span>
              <span className="font-mono">{countAllNodes(treeData)}</span> lines
            </span>
          </>
        )}
      </section>
    </div>
  );
}
