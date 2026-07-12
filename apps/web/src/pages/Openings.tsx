import { useEffect, useRef, useState, useCallback } from 'react';
import { createPortal } from 'react-dom';
import { useNavigate } from 'react-router-dom';
import { getOpenings, ApiError, type OpeningNode, type ColorFilter } from '../api';
import { useChessUsername } from '../context/ChessUsernameContext';
import { OpeningGraph, type OpeningGraphHandle } from '../components/OpeningGraph';
import { getWinRateColor } from '../utils/openings';
import { useLocalStorage } from '../hooks/useLocalStorage';
import { PageHeader } from '../components/PageHeader';
import { DataStateEmpty, DataStateLoading } from '../components/DataState';

function countAllNodes(node: OpeningNode): number {
  let count = 1;
  if (node.children) {
    for (const child of node.children) count += countAllNodes(child);
  }
  return count;
}

export default function Openings() {
  const navigate = useNavigate();
  const graphRef = useRef<OpeningGraphHandle>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const { username } = useChessUsername();
  const [colorFilter, setColorFilter] = useLocalStorage<ColorFilter>(
    'knightmind:openings:color_filter',
    'both'
  );
  const [treeData, setTreeData] = useState<OpeningNode | null>(null);
  const [tooltip, setTooltip] = useState<{ x: number; y: number; data: OpeningNode } | null>(null);

  // Redirect if no username (username is set during onboarding)
  useEffect(() => {
    if (!username) navigate('/');
  }, [username, navigate]);

  const subtitle = (() => {
    if (loading) {
      return 'Building your opening tree...';
    }
    if (treeData) {
      return `${username}\u2019s openings across all imported games`;
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
        // `message` is the user-facing text; `detail` is technical and logged only.
        if (err.detail) console.error('[openings]', err.detail);
        setError(err.message);
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
      <PageHeader title="Opening Explorer" subtitle={subtitle} />

      {/* Controls */}
      <section className="flex flex-wrap gap-4 items-center px-5 py-3 border border-primary/10 rounded-sm bg-primary/5 backdrop-blur-sm">
        <div>
          <select
            value={colorFilter}
            onChange={(e) => setColorFilter(e.target.value as ColorFilter)}
            className="bg-transparent border-b border-primary/20 py-1 text-primary focus:outline-none focus:border-primary/60 transition-colors font-serif text-lg cursor-pointer"
          >
            <option value="both">All games</option>
            <option value="white">As white</option>
            <option value="black">As black</option>
          </select>
        </div>

        <button
          type="button"
          onClick={handleFetchClick}
          disabled={loading}
          className={[
            'px-6 py-2 rounded-sm font-serif transition-all km-focus-visible',
            loading ? 'km-interactive-disabled disabled:opacity-50' : 'km-interactive',
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
      <section className="relative min-h-[300px] md:min-h-[500px] max-h-[70vh] bg-primary/5 border border-primary/10 rounded-sm overflow-hidden">
        {!treeData && !loading && !error && (
          <div className="absolute inset-0 flex items-center justify-center px-6">
            <div className="w-full max-w-xl">
              <DataStateEmpty
                title="No opening data loaded"
                description="Load your openings to visualize your repertoire and performance by move."
                actionLabel="Load Openings"
                onAction={handleFetchClick}
              />
            </div>
          </div>
        )}

        {loading && (
          <div className="absolute inset-0 flex items-center justify-center px-6">
            <div className="w-full max-w-xl">
              <DataStateLoading label="Tracing paths..." />
            </div>
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
            graphRef={graphRef}
          />
        )}

        {/* Toolbar */}
        {treeData && !loading && (
          <div className="absolute top-3 right-3 flex gap-1">
            <button
              type="button"
              onClick={() => graphRef.current?.zoomIn()}
              className="p-2 bg-bg-primary border border-primary/10 rounded-sm text-primary/60 hover:text-primary km-interactive km-focus-visible transition-colors"
              aria-label="Zoom in"
              title="Zoom in"
            >
              <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                <path d="M12 6v12M6 12h12" />
              </svg>
            </button>
            <button
              type="button"
              onClick={() => graphRef.current?.zoomOut()}
              className="p-2 bg-bg-primary border border-primary/10 rounded-sm text-primary/60 hover:text-primary km-interactive km-focus-visible transition-colors"
              aria-label="Zoom out"
              title="Zoom out"
            >
              <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                <path d="M6 12h12" />
              </svg>
            </button>
            <button
              type="button"
              onClick={() => graphRef.current?.fitToView()}
              className="p-2 bg-bg-primary border border-primary/10 rounded-sm text-primary/60 hover:text-primary km-interactive km-focus-visible transition-colors"
              aria-label="Fit to view"
              title="Fit to view"
            >
              <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                <path d="M4 8V4h4M20 8V4h-4M4 16v4h4M20 16v4h-4" />
              </svg>
            </button>
          </div>
        )}

        {treeData && !loading && (
          <div className="absolute bottom-3 left-3 group">
            <div
              className="w-6 h-6 rounded-full border border-primary/20 flex items-center justify-center text-primary/40 hover:text-primary/70 hover:border-primary/40 transition-colors cursor-help"
              style={{ backgroundColor: 'var(--bg-primary)' }}
            >
              <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                <path d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
              </svg>
            </div>
            <div className="absolute bottom-8 left-0 hidden group-hover:block text-xs text-primary/70 font-sans px-3 py-2 rounded-sm border border-primary/10 shadow-lg whitespace-nowrap" style={{ backgroundColor: 'var(--bg-primary)' }}>
              Scroll to zoom · Drag to pan · Click nodes to expand
            </div>
          </div>
        )}

        {/* Tooltip in portal so it is not clipped by overflow */}
        {tooltip &&
          createPortal(
            <div
              className="fixed z-[9999] border border-primary/20 p-4 shadow-2xl rounded-sm pointer-events-none min-w-[200px]"
              style={{ left: tooltip.x, top: tooltip.y, backgroundColor: 'var(--bg-primary)' }}
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

        <span className="border-l border-primary/20 pl-6 ml-2">Node size = games played</span>

        {treeData && (
          <div className="w-full flex justify-center gap-12 pt-4 mt-4 border-t border-primary/10">
            {[
              { value: treeData.games_count, label: 'Games Analyzed' },
              { value: countAllNodes(treeData), label: 'Unique Positions' },
            ].map(stat => (
              <div className="text-center" key={stat.label}>
                <div className="text-2xl font-mono text-primary">{stat.value}</div>
                <div className="text-xs uppercase tracking-widest text-primary/40">{stat.label}</div>
              </div>
            ))}
          </div>
        )}
      </section>
    </div>
  );
}
