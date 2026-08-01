import { useEffect, useRef, useState, useCallback, useLayoutEffect, useMemo } from 'react';
import { createPortal } from 'react-dom';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import {
  getOpenings, ApiError, DEPTH_OPTIONS, DEFAULT_MAX_PLY, depthLabel, normaliseDepth,
  offeredDepth, offeredColor, getBaseline,
  PERIOD_OPTIONS, DEFAULT_PERIOD, periodLabel, normalisePeriod, offeredPeriod, periodParam,
  type OpeningNode, type ColorFilter, type OpeningBaseline, type Period,
} from '../api';
import { useChessUsername } from '../context/ChessUsernameContext';
import { OpeningGraph, type OpeningGraphHandle, type NodeAnchor } from '../components/OpeningGraph';
import { getScoreColor } from '../utils/openings';
import { formatSigned } from '../utils/ratings';
import {
  formatLine, engineHrefForPath, resolveMoves, encodeLine, decodeLine, pathMoves, fenForPath,
} from '../utils/openingLine';
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
const PERIOD_STORAGE_KEY = 'knightmind:openings:period';

function parsePersistedPeriod(value: string): Period {
  try {
    return normalisePeriod(JSON.parse(value));
  } catch {
    return DEFAULT_PERIOD;
  }
}

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

/**
 * One line comparing a score against players at the same rating.
 *
 * Returns null when there is simply nothing to say, so the panel stays quiet
 * rather than carrying a row that explains its own absence. The thin-sample
 * case is the exception: it *is* worth saying, because the alternative is a
 * reader assuming the comparison was omitted for a reason that flatters them.
 */
function describePeerBaseline(
  baseline: OpeningBaseline | null,
  score: number,
  colorFilter: ColorFilter
): string | null {
  if (colorFilter === 'both') {
    // Not an error state: the figure genuinely cannot exist here, and saying
    // which control produces it is more useful than showing nothing.
    return 'Filter by colour to compare with your rating';
  }
  if (!baseline) return null;
  if (baseline.expected_score === null) {
    return `Too rare to compare (${baseline.games} games)`;
  }
  const where = baseline.band ? ` (${baseline.band.label})` : ' (all ratings)';
  return `${formatDelta(score - baseline.expected_score)} vs ${baseline.expected_score}% expected${where}`;
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
  const [searchParams, setSearchParams] = useSearchParams();
  const [storedColor, setStoredColor] = useLocalStorage<ColorFilter>(
    'knightmind:openings:color_filter',
    'both'
  );
  // Depth was hardcoded at 12 ply with no control, while the API accepts 40 —
  // the explorer simply refused to follow games past six moves.
  // Validation lives in the storage parser rather than at read time, so a bad
  // persisted value is also repaired in place instead of being re-clamped on
  // every render.
  const [storedMaxPly, setStoredMaxPly] = useLocalStorage<number>(
    MAX_PLY_STORAGE_KEY,
    DEFAULT_MAX_PLY,
    parsePersistedMaxPly
  );
  const [storedPeriod, setStoredPeriod] = useLocalStorage<Period>(
    PERIOD_STORAGE_KEY,
    DEFAULT_PERIOD,
    parsePersistedPeriod
  );

  const [treeData, setTreeData] = useState<OpeningNode | null>(null);
  // Which colour the tree on screen actually answers for. The graph is keyed on
  // this rather than on `colorFilter`, because a refresh deliberately keeps the
  // old tree while the new one loads — keying on the control would remount the
  // graph immediately and let it auto-collapse and fit against the *previous*
  // colour's data, then skip both when the real tree arrived.
  // Identity of the tree on screen — which colour *and* which window it
  // answers for. Both are questions rather than refreshes: switching from five
  // years to thirty days can turn a wide tree into three nodes, and a view
  // fitted to the old one then frames mostly empty space.
  const [loadedView, setLoadedView] = useState<string | null>(null);
  const [tooltip, setTooltip] = useState<{ anchor: NodeAnchor; data: OpeningNode } | null>(null);
  const tooltipRef = useRef<HTMLDivElement>(null);
  /** Line the graph has already been told to open, so a refetch does not
   *  re-open it and drag the view back mid-read. Keyed by the loaded colour
   *  too, because that remounts the graph and empties what it knows. */
  const revealedRef = useRef<string | null>(null);
  const online = useOnlineStatus();
  const request = useLatestRequest();

  // The URL says what the view *is*; localStorage only supplies a default for a
  // visit that does not name one. Held this way round, a link carries a view to
  // another person or another device — previously the whole thing lived in
  // per-device storage, so the page looked identical whatever URL you sent.
  const colorFilter = offeredColor(searchParams.get('color')) ?? storedColor;
  const maxPly = offeredDepth(searchParams.get('depth')) ?? storedMaxPly;
  // `?? ` will not do here: "all time" is a window the user chose and is
  // itself null, so absent has to be undefined to fall through to storage.
  const urlPeriod = offeredPeriod(searchParams.get('period'));
  const period = urlPeriod === undefined ? storedPeriod : urlPeriod;
  // Read with `get`, not a truthiness check: the root *is* a selectable line
  // ("Starting position", with its own Engine link), and it encodes to the
  // empty string. Absent and present-but-empty have to stay distinguishable,
  // so the parameter is null when missing and '' when the root is selected.
  const lineParam = searchParams.get('line');

  const updateParams = useCallback(
    (mutate: (params: URLSearchParams) => void, replace: boolean) => {
      setSearchParams(previous => {
        const next = new URLSearchParams(previous);
        mutate(next);
        return next;
      }, { replace });
    },
    [setSearchParams]
  );

  // Controls replace rather than push: nobody wants six presses of Back to
  // walk out through their own filter fiddling. Selecting a line does push —
  // see `handleNodeSelect`.
  const setColorFilter = useCallback((next: ColorFilter) => {
    setStoredColor(next);
    updateParams(params => params.set('color', next), true);
  }, [setStoredColor, updateParams]);

  const setMaxPly = useCallback((next: number) => {
    setStoredMaxPly(next);
    updateParams(params => params.set('depth', String(next)), true);
  }, [setStoredMaxPly, updateParams]);

  const setPeriod = useCallback((next: Period) => {
    setStoredPeriod(next);
    updateParams(params => params.set('period', periodParam(next)), true);
  }, [setStoredPeriod, updateParams]);

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

  // Derived, not stored. The moves in the URL are the durable identity of a
  // line; the nodes are not, so re-walking them against whatever tree is
  // currently loaded is both how a link is restored *and* how a selection is
  // kept honest across a refresh or a colour change. Holding it in state as
  // well would need the two to be synced, and any two-way sync between state
  // and the URL fights itself: each side sees the mismatch and writes.
  const selectedPath = useMemo(
    () => (treeData && lineParam !== null ? resolveMoves(treeData, decodeLine(lineParam)) : null),
    [treeData, lineParam]
  );

  // The line named in the URL is not in the tree now on screen — a link to a
  // Black line opened under the White filter, or a depth too shallow to reach
  // it. The panel is already gone; drop the parameter too, so what is in the
  // address bar keeps matching what is on the page.
  useEffect(() => {
    if (lineParam !== null && treeData && !selectedPath) {
      updateParams(params => params.delete('line'), true);
    }
  }, [lineParam, treeData, selectedPath, updateParams]);

  // Fill in whatever the URL does not say, so copying it always yields the
  // view you are actually looking at rather than the next reader's defaults.
  useEffect(() => {
    if (
      searchParams.has('color') && searchParams.has('depth') && searchParams.has('period')
    ) return;
    updateParams(params => {
      if (!params.has('color')) params.set('color', colorFilter);
      if (!params.has('depth')) params.set('depth', String(maxPly));
      if (!params.has('period')) params.set('period', periodParam(period));
    }, true);
  }, [searchParams, colorFilter, maxPly, period, updateParams]);

  // Open the line in the graph when it was not the graph that chose it —
  // a shared link, a reload, or Back. `handleNodeSelect` marks the line as
  // already shown, so a click here does not bounce the view.
  useEffect(() => {
    if (!selectedPath) return;
    const revealed = `${loadedView}:${encodeLine(selectedPath)}`;
    if (revealed === revealedRef.current) return;
    revealedRef.current = revealed;
    graphRef.current?.revealPath(pathMoves(selectedPath));
  }, [selectedPath, loadedView]);

  // What players around this rating score from the selected position. Kept
  // apart from the tree fetch: it is a different question of a different
  // service, it is allowed to fail without touching the page, and a selection
  // changes far more often than the tree does.
  const [peerBaseline, setPeerBaseline] = useState<OpeningBaseline | null>(null);
  const baselineRequest = useLatestRequest();

  // Keyed on the position, not the selection object. A refetch hands down an
  // equal-but-new tree, so the resolved path is a new array every time even
  // when the line is untouched — and depending on it re-asked lichess about a
  // position that had not moved, on every refresh and every window change.
  // A FEN is a string, so an unchanged position is an unchanged dependency.
  const selectedFen = useMemo(
    () => (selectedPath ? fenForPath(selectedPath) : null),
    [selectedPath]
  );

  useEffect(() => {
    setPeerBaseline(null);
    // "Both" mixes games from either side of the board into one figure, so
    // there is no single expectation to compare it against.
    if (!selectedFen || colorFilter === 'both' || !username.trim()) return;

    const token = baselineRequest.begin();
    getBaseline(username, selectedFen, colorFilter, { signal: token.signal })
      .then(result => {
        if (!token.isStale()) setPeerBaseline(result);
      })
      .catch(() => {
        // Deliberately silent. The page is already rendered and useful; a
        // comparison that could not be fetched is a missing extra, not an
        // error worth interrupting anyone over.
      });
  }, [selectedFen, colorFilter, username, baselineRequest]);

  /**
   * Record a selection in the URL.
   *
   * This one pushes: a selection is somewhere you went, so Back should take
   * you out of it. Re-selecting the node already selected is a no-op rather
   * than a second identical history entry.
   */
  const handleNodeSelect = useCallback((path: OpeningNode[] | null) => {
    const line = path ? encodeLine(path) : null;
    if (line === lineParam) return;
    revealedRef.current = `${loadedView}:${line}`;
    updateParams(params => {
      if (line === null) params.delete('line');
      else params.set('line', line);
    }, false);
  }, [lineParam, loadedView, updateParams]);

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
      // The window belongs in the sentence: a tree over 90 days and a tree
      // over five years look identical, and reading the wrong one as the whole
      // picture is the mistake this filter exists to prevent.
      const window = period === null ? '' : `, ${periodLabel(period).toLowerCase()}`;
      return `${username}’s openings ${SCOPE_SUFFIX[colorFilter]}${window}`;
    }
    if (treeData) {
      return `No ${SCOPE_NOUN[colorFilter]} to chart yet.`;
    }
    return 'Load your games to build your opening knowledge graph.';
  })();

  const fetchOpenings = useCallback(async (user: string, color: ColorFilter, plies: number, sinceDays: Period) => {
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
      const data = await getOpenings(user, color, plies, sinceDays);
      if (token.isStale()) return;
      setTreeData(data);
      setLoadedView(`${color}:${periodParam(sinceDays)}`);
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
    fetchOpenings(username, colorFilter, maxPly, period);
  };

  // Auto-fetch when page loads with username or when username/color filter changes
  useEffect(() => {
    if (username.trim()) {
      fetchOpenings(username, colorFilter, maxPly, period);
    }
  }, [username, colorFilter, maxPly, period, fetchOpenings]);

  /**
   * A 200 with an empty tree has several distinct causes; each needs a
   * different next step, so name the actual one rather than showing a generic
   * "no data" card.
   */
  const emptyState = (() => {
    // Ordered by what is actually true, not by convenience: with games both
    // excluded by the filter AND skipped as unreadable, blaming the filter
    // alone is simply wrong, and the accurate branch was unreachable.
    // First, because when nothing reached the builder at all the window is the
    // whole story — no other filter got a chance to be the cause. Sending
    // someone with a four-hundred-game archive to the import screen because
    // they took a month off is the failure this branch exists to prevent.
    if ((analysis?.games_seen ?? 0) === 0 && (analysis?.excluded_by_date ?? 0) > 0) {
      return {
        title: `No games ${periodLabel(period).toLowerCase()}`,
        description: `You have ${analysis?.excluded_by_date} imported ${
          analysis?.excluded_by_date === 1 ? 'game' : 'games'
        }, none of them in this period. Widen it to see the rest of your repertoire.`,
        actionLabel: 'Show all time',
        onAction: () => setPeriod(null),
      };
    }
    if (
      colorFilter !== 'both' &&
      (analysis?.excluded_by_color ?? 0) > 0 &&
      skippedGames === 0
    ) {
      const side = colorFilter === 'white' ? 'White' : 'Black';
      return {
        title: `No ${SCOPE_NOUN[colorFilter]} yet`,
        description: `None of your imported games were played as ${side}. Switch back to all games to see the rest of your repertoire.`,
        actionLabel: 'Show all games',
        onAction: () => setColorFilter('both'),
      };
    }
    if (skippedGames > 0) {
      const excluded = analysis?.excluded_by_color ?? 0;
      const alsoFiltered = excluded > 0
        ? ` A further ${excluded} were played as the other colour.`
        : '';
      return {
        title: 'None of your games could be analysed',
        description: `${skippedGames} of ${analysis?.games_stored ?? skippedGames} stored games were unreadable, unfinished, or played under a different username.${alsoFiltered} Re-importing usually fixes this.`,
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
    const peerLine = describePeerBaseline(peerBaseline, node.win_rate, colorFilter);

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
            {/* Your own average says whether a line is bad *for you*; it cannot
                say whether it is bad. Players at the same rating scoring 52%
                from the same position is the difference between "this opening
                is hard" and "I am playing this opening badly". */}
            {peerLine && (
              <dd className="font-sans text-xs text-primary/70 mt-0.5 whitespace-nowrap">
                {peerLine}
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
            onClick={() => handleNodeSelect(null)}
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
        // Remount when the loaded colour or window changes — a different
        // question deserves a fresh view. A plain Refresh keeps the same
        // instance, and so keeps the user's zoom and expanded lines.
        key={loadedView ?? 'initial'}
        data={treeData as OpeningNode}
        onNodeHover={(anchor, node) => setTooltip({ anchor, data: node })}
        onNodeHoverEnd={() => setTooltip(null)}
        onNodeSelect={handleNodeSelect}
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

          <div>
            <select
              value={periodParam(period)}
              onChange={(e) => setPeriod(offeredPeriod(e.target.value) ?? DEFAULT_PERIOD)}
              aria-label="Time period covered"
              className="bg-transparent border-b border-primary/20 py-1 text-primary focus:outline-none focus:border-primary/60 transition-colors font-serif text-lg cursor-pointer"
            >
              {PERIOD_OPTIONS.map(days => (
                <option key={periodParam(days)} value={periodParam(days)}>
                  {periodLabel(days)}
                </option>
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
