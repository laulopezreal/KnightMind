import { useEffect, useRef, useState, useCallback, useLayoutEffect } from 'react';
import { createPortal } from 'react-dom';
import { useNavigate } from 'react-router-dom';
import { getOpenings, ApiError, type OpeningNode, type ColorFilter } from '../api';
import { useChessUsername } from '../context/ChessUsernameContext';
import { OpeningGraph, type OpeningGraphHandle, type NodeAnchor } from '../components/OpeningGraph';
import { getScoreColor } from '../utils/openings';
import { useLocalStorage } from '../hooks/useLocalStorage';
import { PageHeader } from '../components/PageHeader';
import {
  DataStateEmpty,
  DataStateError,
  DataStateLoading,
  DataStateOffline,
  DataStatePartial,
} from '../components/DataState';
import { useOnlineStatus } from '../hooks/useOnlineStatus';
import { useLatestRequest } from '../hooks/useLatestRequest';

/** Trailing clause for the page subtitle, e.g. "alice's openings in games as White". */
const SCOPE_SUFFIX: Record<ColorFilter, string> = {
  both: 'across all imported games',
  white: 'in games as White',
  black: 'in games as Black',
};

/** Noun phrase for empty-state copy, e.g. "No games as Black yet". */
const SCOPE_NOUN: Record<ColorFilter, string> = {
  both: 'games',
  white: 'games as White',
  black: 'games as Black',
};

/** Modifier key d3-zoom listens for, named the way the user's keyboard labels it. */
const ZOOM_MODIFIER = /Mac|iPhone|iPad/i.test(
  typeof navigator === 'undefined' ? '' : navigator.userAgent
) ? '⌘' : 'Ctrl';

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
  // Distinct from `error`: the account simply has nothing imported yet. That is
  // a first-run state with a real next step, not a failure with a Retry that
  // can only fail again.
  const [noGamesImported, setNoGamesImported] = useState(false);
  const { username } = useChessUsername();
  const [colorFilter, setColorFilter] = useLocalStorage<ColorFilter>(
    'knightmind:openings:color_filter',
    'both'
  );
  const [treeData, setTreeData] = useState<OpeningNode | null>(null);
  const [tooltip, setTooltip] = useState<{ anchor: NodeAnchor; data: OpeningNode } | null>(null);
  const tooltipRef = useRef<HTMLDivElement>(null);
  const online = useOnlineStatus();
  const request = useLatestRequest();

  // Clamp the tooltip into the viewport after it renders. Anchoring it blindly
  // to the node put up to 180px of it off-screen for any node in the lower
  // right — reachable simply by panning.
  useLayoutEffect(() => {
    const el = tooltipRef.current;
    if (!tooltip || !el) return;
    const { width, height } = el.getBoundingClientRect();
    const margin = 8;
    const clamp = (value: number, max: number) =>
      Math.min(Math.max(margin, value), Math.max(margin, max - margin));

    // Prefer the right of the node; flip to its left when that would overflow.
    // Both axes are then hard-clamped, so the card is on screen even for a node
    // panned right to the edge.
    const preferredX = tooltip.anchor.x + width + margin > window.innerWidth
      ? tooltip.anchor.x - width - 24
      : tooltip.anchor.x;
    const x = clamp(preferredX, window.innerWidth - width);
    const y = clamp(tooltip.anchor.y - height / 2, window.innerHeight - height);
    el.style.left = `${x}px`;
    el.style.top = `${y}px`;
    el.style.visibility = 'visible';
  }, [tooltip]);

  // Redirect if no username (username is set during onboarding)
  useEffect(() => {
    if (!username) navigate('/');
  }, [username, navigate]);

  // The API always answers a 200 with a root node, even when nothing matched —
  // so "did anything load" must be judged on the contents, not the container.
  const analysis = treeData?.analysis;
  const hasOpenings = treeData !== null && treeData.games_count > 0;
  const skippedGames = analysis?.games_skipped ?? 0;

  // Before the first response there is nothing to show but a spinner; treating
  // that as loading avoids a frame of bogus "no data" chrome on mount.
  const showLoading = loading || (!treeData && !error && !noGamesImported);

  const subtitle = (() => {
    if (showLoading) {
      return 'Building your opening tree...';
    }
    if (noGamesImported) {
      return 'Import your games to build your opening knowledge graph.';
    }
    if (hasOpenings) {
      return `${username}’s openings ${SCOPE_SUFFIX[colorFilter]}`;
    }
    if (treeData) {
      return `No ${SCOPE_NOUN[colorFilter]} to chart yet.`;
    }
    return 'Load your games to build your opening knowledge graph.';
  })();

  const fetchOpenings = useCallback(async (user: string, color: ColorFilter) => {
    // The route guard above redirects when there is no username, so there is
    // nothing actionable to say here.
    if (!user.trim()) return;

    // Guard against stale-response races: a username/color change begins a newer
    // request; the older, slower response must not clobber the newer one.
    const token = request.begin();
    setLoading(true);
    setError(null);
    setNoGamesImported(false);

    try {
      const data = await getOpenings(user, color);
      if (token.isStale()) return;
      setTreeData(data);
    } catch (err) {
      if (token.isStale()) return;
      if (err instanceof ApiError) {
        // `message` is the user-facing text; `detail` is technical and logged only.
        if (err.detail) console.error('[openings]', err.detail);
        if (err.statusCode === 404) {
          setNoGamesImported(true);
        } else {
          setError(err.message);
        }
      } else {
        setError(err instanceof Error ? err.message : 'Failed to load openings');
      }
      setTreeData(null);
    } finally {
      if (!token.isStale()) setLoading(false);
    }
  }, [request]);

  const handleFetchClick = () => {
    fetchOpenings(username, colorFilter);
  };

  // Auto-fetch when page loads with username or when username/color filter changes
  useEffect(() => {
    if (username.trim()) {
      fetchOpenings(username, colorFilter);
    }
  }, [username, colorFilter, fetchOpenings]);

  /**
   * A 200 with an empty tree has several distinct causes; each needs a
   * different next step, so name the actual one rather than showing a generic
   * "no data" card.
   */
  const emptyState = (() => {
    if (colorFilter !== 'both' && (analysis?.excluded_by_color ?? 0) > 0) {
      const side = colorFilter === 'white' ? 'White' : 'Black';
      return {
        title: `No ${SCOPE_NOUN[colorFilter]} yet`,
        description: `None of your imported games were played as ${side}. Switch back to all games to see the rest of your repertoire.`,
        actionLabel: 'Show all games',
        onAction: () => setColorFilter('both'),
      };
    }
    if (skippedGames > 0) {
      return {
        title: 'None of your games could be analysed',
        description: `${skippedGames} of ${analysis?.games_stored ?? skippedGames} stored games were unreadable, unfinished, or played under a different username. Re-importing usually fixes this.`,
        actionLabel: 'Re-import games',
        onAction: () => navigate('/'),
      };
    }
    return {
      title: 'No opening data yet',
      description: 'Import your Chess.com games and KnightMind will chart the openings you actually play.',
      actionLabel: 'Import games',
      onAction: () => navigate('/'),
    };
  })();

  const graphPanel = (
    // A definite height, not just a min-height: the SVG's height="100%" had no
    // definite parent to resolve against, so it fell back to its viewBox aspect
    // ratio and rendered 231px tall inside a 500px panel — over half the panel
    // wasted while the tree was squeezed into the rest.
    <section className="relative h-[60vh] min-h-[360px] max-h-[720px] bg-primary/5 border border-primary/10 rounded-sm overflow-hidden">
      <OpeningGraph
        data={treeData as OpeningNode}
        onNodeHover={(anchor, node) => setTooltip({ anchor, data: node })}
        onNodeHoverEnd={() => setTooltip(null)}
        onError={setError}
        graphRef={graphRef}
      />

      {/* Toolbar */}
      <div className="absolute top-3 right-3 flex gap-1">
        <button
          type="button"
          onClick={() => graphRef.current?.zoomIn()}
          className="p-2 bg-bg-primary border border-primary/10 rounded-sm text-primary/70 hover:text-primary km-interactive km-focus-visible transition-colors"
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
          className="p-2 bg-bg-primary border border-primary/10 rounded-sm text-primary/70 hover:text-primary km-interactive km-focus-visible transition-colors"
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
          className="p-2 bg-bg-primary border border-primary/10 rounded-sm text-primary/70 hover:text-primary km-interactive km-focus-visible transition-colors"
          aria-label="Fit to view"
          title="Fit to view"
        >
          <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
            <path d="M4 8V4h4M20 8V4h-4M4 16v4h4M20 16v4h-4" />
          </svg>
        </button>
      </div>

      {/* A real button, so the controls are readable without a mouse — this was
          a bare div revealed only by CSS :hover. */}
      <div className="absolute bottom-3 left-3 group">
        <button
          type="button"
          aria-describedby="opening-graph-help"
          aria-label="Graph controls help"
          className="w-6 h-6 rounded-full border border-primary/20 flex items-center justify-center text-primary/70 hover:text-primary hover:border-primary/40 focus-visible:border-primary/40 transition-colors cursor-help km-focus-visible"
          style={{ backgroundColor: 'var(--bg-primary)' }}
        >
          <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2} aria-hidden="true">
            <path d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
          </svg>
        </button>
        <div
          id="opening-graph-help"
          role="tooltip"
          className="absolute bottom-8 left-0 hidden group-hover:block group-focus-within:block text-xs text-primary/70 font-sans px-3 py-2 rounded-sm border border-primary/10 shadow-lg whitespace-nowrap"
          style={{ backgroundColor: 'var(--bg-primary)' }}
        >
          Drag to pan · {ZOOM_MODIFIER}+scroll to zoom · Click or press Enter to expand · Arrow keys to navigate
        </div>
      </div>
    </section>
  );

  return (
    <div className="space-y-8 animate-teedin">
      <PageHeader title="Opening Explorer" subtitle={subtitle} />

      {/* Controls — nothing to filter until games exist, so the first-run state
          shows a single Import call to action instead. */}
      {!noGamesImported && (
        <section className="flex flex-wrap gap-4 items-center px-5 py-3 border border-primary/10 rounded-sm bg-primary/5 backdrop-blur-sm">
          <div>
            <select
              value={colorFilter}
              onChange={(e) => setColorFilter(e.target.value as ColorFilter)}
              aria-label="Filter openings by color played"
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
      )}

      {/* Error */}
      {error && (
        !online ? (
          // A failed load while the browser is offline is a connectivity problem,
          // not a server error — say so instead of a bare error message.
          <DataStateOffline onRetry={handleFetchClick} compact />
        ) : (
          <DataStateError
            message={error}
            onRetry={handleFetchClick}
            retryLabel="Retry"
            ariaLabel="Retry loading openings"
            compact
          />
        )
      )}

      {showLoading && (
        <section className="relative min-h-[300px] md:min-h-[500px] bg-primary/5 border border-primary/10 rounded-sm overflow-hidden">
          <div className="absolute inset-0 flex items-center justify-center px-6">
            <div className="w-full max-w-xl">
              <DataStateLoading label="Tracing paths..." />
            </div>
          </div>
        </section>
      )}

      {/* Nothing imported yet — a first run, not an error. */}
      {!showLoading && noGamesImported && (
        <DataStateEmpty
          title="No games imported yet"
          description="Import your Chess.com games and KnightMind will chart the openings you actually play."
          actionLabel="Import games"
          onAction={() => navigate('/')}
        />
      )}

      {/* Loaded, but the tree is empty. Explain which of the several possible
          reasons applies rather than rendering empty graph chrome. */}
      {!showLoading && !error && treeData && !hasOpenings && (
        <DataStateEmpty
          title={emptyState.title}
          description={emptyState.description}
          actionLabel={emptyState.actionLabel}
          onAction={emptyState.onAction}
        />
      )}

      {!showLoading && hasOpenings && (
        <>
          {skippedGames > 0 ? (
            <DataStatePartial
              message={`${skippedGames} of ${analysis?.games_stored ?? '?'} stored games could not be analysed (unreadable, unfinished, or played under a different username), so they are missing from this tree.`}
              onRetry={handleFetchClick}
              retryLabel="Reload"
              retryPending={loading}
            >
              {graphPanel}
            </DataStatePartial>
          ) : (
            graphPanel
          )}

          {/* Legend + Stats — only meaningful alongside a rendered graph. */}
          <section className="flex flex-wrap gap-6 items-center justify-center text-xs font-sans text-primary/70">
            <span className="uppercase tracking-widest mr-2">Score:</span>
            <div className="flex items-center gap-1"><div className="w-3 h-3 rounded-full bg-emerald-600"></div> 60%+</div>
            <div className="flex items-center gap-1"><div className="w-3 h-3 rounded-full bg-emerald-500"></div> 50%+</div>
            <div className="flex items-center gap-1"><div className="w-3 h-3 rounded-full bg-lime-500"></div> 45%+</div>
            <div className="flex items-center gap-1"><div className="w-3 h-3 rounded-full bg-yellow-500"></div> 40%+</div>
            <div className="flex items-center gap-1"><div className="w-3 h-3 rounded-full bg-orange-500"></div> 30%+</div>
            <div className="flex items-center gap-1"><div className="w-3 h-3 rounded-full bg-red-500"></div> &lt;30%</div>

            <span className="border-l border-primary/20 pl-6 ml-2">Node size = games played</span>

            {/* Spelled out because a line drawn every time scores 50% while
                winning none — "win rate" would read as a contradiction. */}
            <span className="w-full text-center text-primary/70">
              Score = (wins + &frac12; draws) &divide; games played
            </span>

            <div className="w-full flex justify-center gap-12 pt-4 mt-4 border-t border-primary/10">
              {[
                { value: treeData.games_count, label: 'Games Analyzed' },
                // Tree nodes are distinct move sequences, not distinct positions:
                // two transposing lines reach one position via two nodes.
                { value: countAllNodes(treeData) - 1, label: 'Move Sequences' },
              ].map(stat => (
                <div className="text-center" key={stat.label}>
                  <div className="text-2xl font-mono text-primary">{stat.value}</div>
                  <div className="text-xs uppercase tracking-widest text-primary/70">{stat.label}</div>
                </div>
              ))}
            </div>
          </section>
        </>
      )}

      {/* Tooltip in portal so it is not clipped by overflow */}
      {tooltip &&
        createPortal(
          <div
            ref={tooltipRef}
            // The graph node carries the same figures in its aria-label, so this
            // visual echo is hidden from assistive tech rather than announced twice.
            aria-hidden="true"
            className="fixed z-[9999] border border-primary/20 p-4 shadow-2xl rounded-sm pointer-events-none min-w-[200px]"
            style={{
              left: tooltip.anchor.x,
              top: tooltip.anchor.y,
              // Positioned precisely by the layout effect above; kept hidden for
              // the first frame so it never flashes at an unclamped position.
              visibility: 'hidden',
              backgroundColor: 'var(--bg-primary)',
            }}
          >
            <div className="font-serif text-xl text-primary mb-2 border-b border-primary/10 pb-2">
              {tooltip.data.move_san === 'Start' ? 'Start' : tooltip.data.move_san}
            </div>
            <div className="space-y-1 font-sans text-sm text-primary/80">
              <div className="flex justify-between"><span>Games</span> <span>{tooltip.data.games_count}</span></div>
              <div className="flex justify-between text-positive"><span>Won</span> <span>{tooltip.data.wins}</span></div>
              <div className="flex justify-between text-primary/70"><span>Draw</span> <span>{tooltip.data.draws}</span></div>
              <div className="flex justify-between text-negative"><span>Lost</span> <span>{tooltip.data.losses}</span></div>
              <div className="pt-2 border-t border-primary/10 flex justify-between font-medium">
                <span>Score</span>
                <span style={{ color: getScoreColor(tooltip.data.win_rate) }}>{tooltip.data.win_rate}%</span>
              </div>
            </div>
          </div>,
          document.body
        )}
    </div>
  );
}
