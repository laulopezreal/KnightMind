import { useEffect, useRef, useState, useCallback, useLayoutEffect, useMemo } from 'react';
import { createPortal } from 'react-dom';
import { Link, useNavigate } from 'react-router-dom';
import {
  getOpenings, ApiError, DEPTH_OPTIONS, DEFAULT_MAX_PLY, depthLabel, normaliseDepth,
  type OpeningNode, type ColorFilter,
} from '../api';
import { useChessUsername } from '../context/ChessUsernameContext';
import { OpeningGraph, type OpeningGraphHandle, type NodeAnchor } from '../components/OpeningGraph';
import { getScoreColor } from '../utils/openings';
import { formatSigned } from '../utils/ratings';
import { formatLine, engineHrefForPath, resolvePath } from '../utils/openingLine';
import { useLocalStorage } from '../hooks/useLocalStorage';
import { PageHeader } from '../components/PageHeader';
import {
  DataStateEmpty,
  DataStateError,
  DataStateLoading,
  DataStateOffline,
  DataStatePartial,
  DataStateStale,
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

const MAX_PLY_STORAGE_KEY = 'knightmind:openings:max_ply';

function parsePersistedMaxPly(value: string): number {
  let parsed: unknown;
  try {
    parsed = JSON.parse(value);
  } catch {
    parsed = undefined;
  }
  // `normaliseDepth` lives beside DEPTH_OPTIONS, so the option list and the
  // rule that validates it cannot drift apart.
  const maxPly = normaliseDepth(parsed);
  if (parsed !== maxPly) {
    window.localStorage.setItem(MAX_PLY_STORAGE_KEY, JSON.stringify(maxPly));
  }
  return maxPly;
}

/** Modifier key d3-zoom listens for, named the way the user's keyboard labels it. */
const ZOOM_MODIFIER = /Mac|iPhone|iPad/i.test(
  typeof navigator === 'undefined' ? '' : navigator.userAgent
) ? '⌘' : 'Ctrl';

/**
 * Signed difference against the baseline, e.g. "-35.6" or "+4.2".
 * Delegates to the shared delta formatter so this reads like every other delta
 * in the app; only the exactly-equal case is Openings-specific, since "+0" is
 * a worse answer than saying so.
 */
function formatDelta(delta: number): string {
  const rounded = Math.round(delta * 10) / 10;
  if (rounded === 0) return 'level with';
  return formatSigned(rounded, 1);
}

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
  // Depth was hardcoded at 12 ply with no control, while the API accepts 40 —
  // the explorer simply refused to follow games past six moves.
  // Validation lives in the storage parser rather than at read time, so a bad
  // persisted value is also repaired in place instead of being re-clamped on
  // every render.
  const [maxPly, setMaxPly] = useLocalStorage<number>(
    MAX_PLY_STORAGE_KEY,
    DEFAULT_MAX_PLY,
    parsePersistedMaxPly
  );
  const [treeData, setTreeData] = useState<OpeningNode | null>(null);
  const [tooltip, setTooltip] = useState<{ anchor: NodeAnchor; data: OpeningNode } | null>(null);
  // Root-to-node path for the activated node. Backs the selection panel, which
  // is the page's only stable (non-hover) surface for a line's figures and the
  // only route out of the Opening Explorer into the rest of the app.
  const [selectedPath, setSelectedPath] = useState<OpeningNode[] | null>(null);
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

  // A selection holds nodes from the tree it was made against. Left alone, its
  // figures go stale on refresh and become plain wrong after a colour-filter
  // change — the panel would keep showing "1. e4 c5, 31 games" while the graph
  // beneath it answered a different question. Re-walk the line in the new tree
  // to pick up current numbers, or drop it when the line is not in this one.
  useEffect(() => {
    setSelectedPath(previous => {
      if (!previous) return previous;
      if (!treeData) return null;
      return resolvePath(treeData, previous);
    });
  }, [treeData]);

  // The API always answers a 200 with a root node, even when nothing matched —
  // so "did anything load" must be judged on the contents, not the container.
  const analysis = treeData?.analysis;
  const hasOpenings = treeData !== null && treeData.games_count > 0;
  const skippedGames = analysis?.games_skipped ?? 0;
  // The floor the server actually applied — it raises a shallow request at
  // depth, so reporting what we asked for could contradict the tree on screen.
  const minGames = analysis?.min_games ?? 1;
  // Walking the whole tree on every render was cheap at a fixed 12 plies; the
  // depth control raised the ceiling, and every hover re-renders this page.
  const moveSequences = useMemo(
    () => (treeData ? countAllNodes(treeData) - 1 : 0),
    [treeData]
  );
  // The root aggregates every analysed game, so its score is the user's overall
  // average — the baseline any individual line is judged against.
  const baseline = hasOpenings ? treeData.win_rate : null;

  // Only the *first* load blanks the panel. A refresh keeps the existing tree
  // on screen — replacing it with a spinner threw away the user's zoom and
  // every line they had expanded, for a request that usually returns the same
  // data. See `isRefreshing` for the in-place indicator.
  const showLoading = (loading && !treeData) || (!treeData && !error && !noGamesImported);
  const isRefreshing = loading && treeData !== null;

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

  const fetchOpenings = useCallback(async (user: string, color: ColorFilter, plies: number) => {
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
      const data = await getOpenings(user, color, plies);
      if (token.isStale()) return;
      setTreeData(data);
    } catch (err) {
      if (token.isStale()) return;
      let isMissingGames = false;
      if (err instanceof ApiError) {
        // `message` is the user-facing text; `detail` is technical and logged only.
        if (err.detail) console.error('[openings]', err.detail);
        if (err.statusCode === 404) {
          isMissingGames = true;
          setNoGamesImported(true);
        } else {
          setError(err.message);
        }
      } else {
        setError(err instanceof Error ? err.message : 'Failed to load openings');
      }
      // A failed *refresh* must not discard the tree the user is looking at —
      // that loses their zoom and expanded lines to a transient network blip,
      // which is exactly what keeping the graph mounted during a refresh was
      // meant to prevent. Only a 404 genuinely means "there is nothing here".
      setTreeData(previous => (previous && !isMissingGames ? previous : null));
    } finally {
      if (!token.isStale()) setLoading(false);
    }
  }, [request]);

  const handleFetchClick = () => {
    fetchOpenings(username, colorFilter, maxPly);
  };

  // Auto-fetch when page loads with username or when username/color filter changes
  useEffect(() => {
    if (username.trim()) {
      fetchOpenings(username, colorFilter, maxPly);
    }
  }, [username, colorFilter, maxPly, fetchOpenings]);

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

  /**
   * Details for the activated node.
   *
   * Two jobs: a stable, non-hover place to read a line's figures (the tooltip
   * vanishes the moment you move the pointer, and never existed for touch),
   * and the page's only way out into the rest of the app — the Opening
   * Explorer used to be a dead end with no outbound link at all.
   */
  const selectionPanel = (() => {
    if (!selectedPath) return null;
    const node = selectedPath[selectedPath.length - 1];
    const engineHref = engineHrefForPath(selectedPath);

    return (
      <section
        aria-label="Selected line"
        className="flex flex-wrap items-center gap-x-8 gap-y-4 px-5 py-4 border border-primary/10 rounded-sm bg-primary/5 backdrop-blur-sm"
      >
        <div className="min-w-[12rem] flex-1">
          <p className="text-xs uppercase tracking-widest text-primary/70 mb-1">Selected line</p>
          {/* The name leads and the moves follow: a repertoire you can only
              read as bare SAN is one you cannot search for or talk about. */}
          {node.opening_name && (
            <p className="font-serif text-primary text-lg leading-snug">
              {node.opening_name}
              {node.eco && (
                <span className="font-mono text-xs text-primary/70 ml-2 align-middle">{node.eco}</span>
              )}
            </p>
          )}
          <p className="font-mono text-primary/80 text-sm break-words">{formatLine(selectedPath)}</p>
        </div>

        <dl className="flex gap-6 font-sans text-sm">
          {[
            { label: 'Games', value: node.games_count, className: 'text-primary' },
            { label: 'Won', value: node.wins, className: 'text-positive' },
            { label: 'Drawn', value: node.draws, className: 'text-primary/70' },
            { label: 'Lost', value: node.losses, className: 'text-negative' },
          ].map(stat => (
            <div key={stat.label}>
              <dt className="text-xs uppercase tracking-widest text-primary/70">{stat.label}</dt>
              <dd className={`font-mono ${stat.className}`}>{stat.value}</dd>
            </div>
          ))}
          <div>
            <dt className="text-xs uppercase tracking-widest text-primary/70">Score</dt>
            <dd className="font-mono" style={{ color: getScoreColor(node.win_rate) }}>
              {node.win_rate}%
            </dd>
            {/* A score with no baseline is not an insight: 41% means nothing
                until you know whether you usually score better or worse. The
                root node is exactly the user's overall average, so the
                comparison needs no data the page does not already have. */}
            {baseline !== null && (
              <dd className="font-sans text-xs text-primary/70 mt-0.5 whitespace-nowrap">
                {formatDelta(node.win_rate - baseline)} vs your {baseline}%
              </dd>
            )}
          </div>
        </dl>

        <div className="flex items-center gap-3">
          {engineHref && (
            <Link
              to={engineHref}
              className="px-4 py-2 border border-primary/20 text-primary rounded-sm font-serif text-sm km-interactive km-focus-visible transition-all"
            >
              Analyse in Engine →
            </Link>
          )}
          <button
            type="button"
            onClick={() => setSelectedPath(null)}
            className="text-xs uppercase tracking-widest text-primary/70 hover:text-primary km-focus-visible transition-colors"
          >
            Clear
          </button>
        </div>
      </section>
    );
  })();

  const graphPanel = (
    // A definite height, not just a min-height: the SVG's height="100%" had no
    // definite parent to resolve against, so it fell back to its viewBox aspect
    // ratio and rendered 231px tall inside a 500px panel — over half the panel
    // wasted while the tree was squeezed into the rest.
    <section className="relative h-[60vh] min-h-[360px] max-h-[720px] bg-primary/5 border border-primary/10 rounded-sm overflow-hidden">
      <OpeningGraph
        // Remounting on a filter change resets the view, which is right for a
        // different question; a plain Refresh keeps the same instance and so
        // keeps the user's zoom and expanded lines.
        key={colorFilter}
        data={treeData as OpeningNode}
        onNodeHover={(anchor, node) => setTooltip({ anchor, data: node })}
        onNodeHoverEnd={() => setTooltip(null)}
        onNodeSelect={setSelectedPath}
        onError={setError}
        graphRef={graphRef}
      />

      {isRefreshing && (
        <div className="absolute top-3 left-3 px-3 py-1.5 rounded-sm border border-primary/10" style={{ backgroundColor: 'var(--bg-primary)' }}>
          <DataStateLoading label="Refreshing…" compact />
        </div>
      )}

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

          <div>
            <select
              value={maxPly}
              onChange={(e) => setMaxPly(Number(e.target.value))}
              aria-label="Tree depth in moves"
              className="bg-transparent border-b border-primary/20 py-1 text-primary focus:outline-none focus:border-primary/60 transition-colors font-serif text-lg cursor-pointer"
            >
              {DEPTH_OPTIONS.map(plies => (
                <option key={plies} value={plies}>{depthLabel(plies)} deep</option>
              ))}
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
      {error && !hasOpenings && (
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
          {/* A failed refresh with a tree still on screen is not a blocking
              failure — the data below is merely older. Framing it politely
              (role="status") beats an assertive alert over content the user
              can still read. It outranks the partial-data notice because it
              describes the more recent, more actionable problem. */}
          {error ? (
            <DataStateStale
              message={online
                ? `Couldn’t refresh: ${error}`
                : 'You appear to be offline, so this could not be refreshed.'}
              onRefresh={handleFetchClick}
              refreshPending={loading}
            >
              {graphPanel}
            </DataStateStale>
          ) : skippedGames > 0 ? (
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

          {selectionPanel}

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
            {/* The ring repeats the score as an angle, so it survives the
                red/green confusion the fill colours are prone to. */}
            <span className="border-l border-primary/20 pl-6">Ring = score</span>

            {/* Spelled out because a line drawn every time scores 50% while
                winning none — "win rate" would read as a contradiction. */}
            <span className="w-full text-center text-primary/70">
              Score = (wins + &frac12; draws) &divide; games played
            </span>

            {/* Deeper trees prune one-off lines, which would otherwise be ~96%
                of the nodes. Stated, never silent — a thinner tree must read as
                a deliberate filter, not as missing data. */}
            {minGames > 1 && (
              <span className="w-full text-center text-primary/70">
                Showing lines played at least {minGames} times
              </span>
            )}

            <div className="w-full flex justify-center gap-12 pt-4 mt-4 border-t border-primary/10">
              {[
                { value: treeData.games_count, label: 'Games Analyzed' },
                // Tree nodes are distinct move sequences, not distinct positions:
                // two transposing lines reach one position via two nodes.
                { value: moveSequences, label: 'Move Sequences' },
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
            <div className="mb-2 border-b border-primary/10 pb-2">
              <div className="font-serif text-xl text-primary">
                {tooltip.data.move_san === 'Start' ? 'Start' : tooltip.data.move_san}
              </div>
              {tooltip.data.opening_name && (
                <div className="font-sans text-xs text-primary/70 mt-0.5 max-w-[16rem]">
                  {tooltip.data.opening_name}
                  {tooltip.data.eco && <span className="font-mono ml-1.5">{tooltip.data.eco}</span>}
                </div>
              )}
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
              {baseline !== null && (
                <div className="flex justify-between text-xs text-primary/70">
                  <span>vs your {baseline}%</span>
                  <span>{formatDelta(tooltip.data.win_rate - baseline)}</span>
                </div>
              )}
            </div>
          </div>,
          document.body
        )}
    </div>
  );
}
