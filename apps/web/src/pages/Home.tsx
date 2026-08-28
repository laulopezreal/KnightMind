import { useEffect, useRef, useState, useCallback, type ReactNode } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { importChessComGames, getImportStatus, validateChessComUser, getUserStatus, type UserStatus } from '../api/users';
import { ApiError } from '../api/core';
import { generatePuzzles } from '../api/puzzles';
import { useChessUsername } from '../context/ChessUsernameContext';
import { formatRelativeTime } from '../utils/time';
import { useJobPolling } from '../hooks/useJobPolling';
import { useLatestRequest } from '../hooks/useLatestRequest';
import { Modal } from '../components/Modal';
import { JobStatusCard } from '../components/JobStatusCard';
import { LoadingSpinner } from '../components/LoadingSpinner';
import { DataStateError, DataStateSkeleton } from '../components/DataState';


type ImportStatus = {
  lastImportedAt: string | null;
  lastNewGames: number | null;
};


type OnboardingPhase = 'idle' | 'importing' | 'generating' | 'complete';

/**
 * Hero heading, rendered in every state — loading, error, loaded — so the page
 * always has a level-one heading (same reasoning as DashboardShell). The
 * subtitle is a slot because its copy varies by state; the h1 never does.
 */
function HomeHero({ children }: { children: ReactNode }) {
  return (
    <section className="space-y-6">
      <h1 className="text-6xl md:text-8xl font-serif text-primary tracking-tight">
        KnightMind
      </h1>
      {children}
    </section>
  );
}

export default function Home() {
  const { username, setUsername } = useChessUsername();
  const navigate = useNavigate();
  const request = useLatestRequest();

  // Page data
  const [userStatus, setUserStatus] = useState<UserStatus | null>(null);
  const [pageLoading, setPageLoading] = useState(true);
  const [pageError, setPageError] = useState<string | null>(null);

  // Import states
  const [importStatus, setImportStatus] = useState<ImportStatus>({
    lastImportedAt: null,
    lastNewGames: null,
  });

  // Inline connect form (new-user CTA — works on all viewports)
  const [showConnect, setShowConnect] = useState(false);
  const [connectInput, setConnectInput] = useState('');
  const [connectValidating, setConnectValidating] = useState(false);
  const [connectError, setConnectError] = useState<string | null>(null);
  const [actionStatus, setActionStatus] = useState<string | null>(null);
  const [isError, setIsError] = useState(false);
  const [loading, setLoading] = useState(false);

  // Onboarding state
  const [onboardingPhase, setOnboardingPhase] = useState<OnboardingPhase>('idle');
  const [generatingJobId, setGeneratingJobId] = useState<string | null>(null);
  // Puzzles actually created by this run, derived from the status delta.
  // `null` = we couldn't determine it, so the modal drops the number rather
  // than guessing.
  const [generatedPuzzleCount, setGeneratedPuzzleCount] = useState<number | null>(null);
  const puzzlesBeforeImportRef = useRef(0);

  // Job polling for puzzle generation
  const { job: generationJob } = useJobPolling(generatingJobId, {
    enabled: onboardingPhase === 'generating',
    onSuccess: async () => {
      // Re-read the status so the celebration can quote the real puzzle count.
      // It used to print `newGamesCount` — the number of GAMES imported — so a
      // 40-game import announced "40 puzzles generated" and then dropped the
      // user on a dashboard showing 6.
      try {
        const freshStatus = await getUserStatus(username);
        setUserStatus(freshStatus);
        setGeneratedPuzzleCount(
          Math.max(0, freshStatus.puzzles_count - puzzlesBeforeImportRef.current),
        );
      } catch {
        // Non-critical: the modal falls back to count-free copy.
        setGeneratedPuzzleCount(null);
      }
      setOnboardingPhase('complete');
      // Show celebration for 3 seconds, then redirect
      setTimeout(() => navigate('/dashboard'), 3000);
    },
    onError: (err) => {
      // The stall error is honest copy ("the job may still be running"), so it
      // must not be framed as a definitive failure; real failures keep the
      // "failed:" prefix. Either way, join the manual-hint sentence without
      // doubling the trailing period.
      const isStall = (err as Error & { isStall?: boolean }).isStall === true;
      const lead = isStall ? err.message : `Puzzle generation failed: ${err.message}`;
      setActionStatus(`${lead.replace(/\.\s*$/, '')}. You can generate them manually from the Puzzles page.`);
      setIsError(true);
      setOnboardingPhase('idle');
      setGeneratingJobId(null);
    }
  });

  // Fetch all page data on mount
  const loadPageData = useCallback(async () => {
    if (!username) {
      // Invalidate anything already in flight. isStale() only turns true when a
      // NEWER request begins, and bailing out here begins none -- so without
      // this, disconnecting the account mid-load lets the old username's
      // response land and render as though it were still theirs.
      request.begin();
      setPageLoading(false);
      return;
    }

    // Guard against stale-response races. This runs on mount AND on every window
    // focus, and the username can change in place via the global editor without
    // remounting -- so a slow response for the previous username could resolve
    // after a newer one and repopulate the page under the new name.
    const token = request.begin();
    setPageLoading(true);
    setPageError(null);

    try {
      const [statusResult, importResult] = await Promise.allSettled([
        getUserStatus(username),
        getImportStatus(username),
      ]);

      if (token.isStale()) return;

      if (statusResult.status === 'fulfilled') {
        setUserStatus(statusResult.value);
      }

      if (importResult.status === 'fulfilled') {
        setImportStatus({
          lastImportedAt: importResult.value.last_imported_at,
          lastNewGames: importResult.value.last_new_games,
        });
      }

      // Only show page error if both requests fail
      if (statusResult.status === 'rejected' && importResult.status === 'rejected') {
        setPageError("We couldn't load your data right now. Please try again.");
      }
    } catch {
      if (token.isStale()) return;
      setPageError("We couldn't load your data right now. Please try again.");
    } finally {
      // The newer request owns the spinner; a superseded one clearing it would
      // show the page as loaded while the real fetch is still running.
      if (!token.isStale()) setPageLoading(false);
    }
  }, [username, request]);

  useEffect(() => {
    loadPageData();
  }, [loadPageData]);

  // Refresh data when window regains focus (e.g. after solving puzzles)
  useEffect(() => {
    const onFocus = () => {
      if (username && onboardingPhase === 'idle') {
        loadPageData();
      }
    };
    window.addEventListener('focus', onFocus);
    return () => window.removeEventListener('focus', onFocus);
  }, [username, onboardingPhase, loadPageData]);

  const handleConnectSave = async () => {
    const trimmed = connectInput.trim();
    if (!trimmed) {
      setConnectError('Enter your Chess.com username');
      return;
    }

    setConnectValidating(true);
    setConnectError(null);

    try {
      const data = await validateChessComUser(trimmed);
      if (!data.valid) {
        setConnectError(data.error || 'User not found on Chess.com');
        return;
      }
      setUsername(data.username || trimmed);
      setShowConnect(false);
    } catch (err) {
      if (err instanceof ApiError) {
        if (err.detail) console.error('[connect]', err.detail);
        setConnectError(err.message || 'Could not validate username');
      } else {
        setConnectError('Could not validate username');
      }
    } finally {
      setConnectValidating(false);
    }
  };

  const handleImport = async () => {
    if (!username.trim()) {
      setActionStatus('Please enter a Chess.com username');
      setIsError(true);
      return;
    }

    // Validate username exists on Chess.com before importing
    setLoading(true);
    setActionStatus('Validating username...');
    setIsError(false);

    try {
      const validation = await validateChessComUser(username.trim());
      if (!validation.valid) {
        setActionStatus('Username not found on Chess.com. Please check spelling.');
        setIsError(true);
        setLoading(false);
        return;
      }
    } catch {
      setActionStatus('Could not validate username. Continuing with import...');
      // Continue anyway if validation fails
    }

    setActionStatus('Fetching games from Chess.com...');
    setOnboardingPhase('importing');

    try {
      const result = await importChessComGames(username);
      let generationFailed = false;

      if (result.games_count === 0 || result.new_games === 0) {
        setActionStatus('No new games — you\u2019re all caught up!');
        setOnboardingPhase('idle');
      } else {
        // Baseline for the "how many puzzles did this actually create?" delta.
        puzzlesBeforeImportRef.current = userStatus?.puzzles_count ?? 0;
        setGeneratedPuzzleCount(null);
        setActionStatus(`${result.new_games} new game${result.new_games === 1 ? '' : 's'} found — generating puzzles...`);
        setOnboardingPhase('generating');

        try {
          const jobResult = await generatePuzzles(username);
          setGeneratingJobId(jobResult.job_id);
        } catch (error) {
          console.error('Failed to start puzzle generation job:', error);
          setActionStatus(
            `Imported ${result.new_games} new games, but puzzle generation failed. You can generate them manually from the Puzzles page.`
          );
          setIsError(true);
          setOnboardingPhase('idle');
          generationFailed = true;
        }
      }
      setImportStatus({
        lastImportedAt: new Date().toISOString(),
        lastNewGames: result.new_games,
      });

      // Refresh user status after successful import
      try {
        const freshStatus = await getUserStatus(username);
        setUserStatus(freshStatus);
      } catch {
        // Non-critical — status will refresh on next page load
      }

      if (!generationFailed) {
        setIsError(false);
      }
    } catch (error) {
      if (error instanceof ApiError) {
        if (error.detail) console.error('[import]', error.detail);
        setActionStatus(error.message);
      } else {
        setActionStatus(error instanceof Error ? error.message : 'Unknown error');
      }
      setIsError(true);
      setOnboardingPhase('idle');
    } finally {
      setLoading(false);
    }
  };

  const isNewUser = !username;
  const hasData = !!userStatus && userStatus.games_count > 0;
  const hasPuzzles = !!userStatus && userStatus.puzzles_count > 0;

  // ── Loading State ──────────────────────────────
  if (pageLoading && username) {
    return (
      <div className="space-y-12 animate-teedin">
        <HomeHero>
          {/* Subtitle copy depends on the data still in flight, so only it is
              skeletoned — the h1 above is real and keeps the page identifiable. */}
          <div className="h-6 w-96 max-w-full bg-primary/5 rounded-sm animate-pulse" aria-hidden="true" />
        </HomeHero>
        <DataStateSkeleton label="Loading your chess data..." className="space-y-12">
          <div className="h-14 w-56 bg-primary/10 rounded-sm animate-pulse" />
          <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
            {[1, 2, 3].map(i => (
              <div key={i} className="h-36 bg-primary/5 border border-primary/10 rounded-sm animate-pulse" />
            ))}
          </div>
        </DataStateSkeleton>
      </div>
    );
  }

  // ── Error State (full-page, when no data loaded) ──
  if (pageError && !userStatus) {
    return (
      <div className="space-y-12 animate-teedin">
        <HomeHero>
          <p className="text-xl font-light text-primary/70 max-w-2xl leading-relaxed">
            Your personal chess intelligence platform.
          </p>
        </HomeHero>
        <div className="max-w-lg">
          <DataStateError
            message={pageError}
            onRetry={loadPageData}
            retryLabel="Retry"
            ariaLabel="Retry loading your data"
            compact
          />
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-16 animate-teedin">
      {/* ── Hero Section ── */}
      <HomeHero>
        {/* Door copy only: Home is the data door (connect, import, sync), so it
            never counts due puzzles — that live training state is the
            Dashboard's job, and duplicating it here made two welcome pages. */}
        <p className="text-xl md:text-2xl font-light text-primary/70 max-w-2xl leading-relaxed">
          {isNewUser
            ? 'Your personal chess intelligence platform. Connect your Chess.com account to begin.'
            : hasData
              ? `Welcome back, ${username}. Keep your games in sync, then pick up your training.`
              : `Connected as ${username}. Import your games to generate personalized puzzles.`
          }
        </p>
      </HomeHero>

      {/* ── Primary CTA Section ── */}
      <section aria-label="Primary action" className="bg-primary/5 border border-primary/10 rounded-sm p-6 md:p-8 max-w-2xl">
        {/* No username → connect account */}
        {isNewUser && (
          <div className="space-y-4 max-w-lg">
            {!showConnect ? (
              <>
                <button
                  type="button"
                  onClick={() => setShowConnect(true)}
                  className="px-8 py-4 bg-primary text-bg-primary rounded-sm text-lg font-serif km-interactive km-focus-visible transition-colors"
                >
                  Connect Chess.com Account
                </button>
                <p className="text-sm font-sans text-primary/70">
                  Import your games and generate puzzles from your real positions.
                </p>
              </>
            ) : (
              <div className="space-y-3">
                <label className="block text-xs font-sans uppercase tracking-widest text-primary/70">
                  Chess.com Username
                </label>
                <div className="flex gap-2">
                  <input
                    autoFocus
                    type="text"
                    value={connectInput}
                    onChange={(e) => setConnectInput(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter') handleConnectSave();
                      if (e.key === 'Escape') setShowConnect(false);
                    }}
                    disabled={connectValidating}
                    placeholder="username"
                    aria-label="Chess.com Username"
                    className="flex-1 min-h-11 bg-primary/5 border border-primary/10 px-3 py-2 text-primary focus:outline-none focus:border-primary/40 rounded-sm transition-colors font-sans"
                  />
                  <button
                    type="button"
                    onClick={handleConnectSave}
                    disabled={connectValidating}
                    className={`min-h-11 px-4 py-2 bg-primary text-bg-primary font-medium rounded-sm transition-opacity km-focus-visible ${connectValidating ? 'km-interactive-disabled' : 'km-interactive'}`}
                  >
                    {connectValidating ? '...' : 'Save'}
                  </button>
                </div>
                {connectError && (
                  <p className="text-xs font-sans text-negative" role="alert">{connectError}</p>
                )}
                <button
                  type="button"
                  onClick={() => setShowConnect(false)}
                  className="text-sm font-sans font-normal text-primary/70 km-interactive km-focus-visible"
                >
                  Cancel
                </button>
              </div>
            )}
          </div>
        )}

        {/* Has username, idle → the door's one job: import/sync. Training and
            due-puzzle state live on the Dashboard/Train pages; Home only points
            onward instead of duplicating their CTA. */}
        {!isNewUser && onboardingPhase === 'idle' && (
          <div className="space-y-4 max-w-lg">
            <div className="flex items-center gap-4 flex-wrap">
              <button
                type="button"
                onClick={handleImport}
                disabled={loading}
                className={`px-8 py-4 bg-primary text-bg-primary rounded-sm text-lg font-serif transition-colors km-focus-visible ${
                  loading ? 'km-interactive-disabled' : 'km-interactive'
                }`}
              >
                {loading ? 'Syncing...' : hasData ? 'Sync New Games' : 'Import Games'}
              </button>
              {importStatus.lastImportedAt && (
                <span className="text-sm font-sans text-primary/70">
                  Last synced {formatRelativeTime(importStatus.lastImportedAt)}
                </span>
              )}
            </div>
            {hasData && (
              <Link
                to="/dashboard"
                className="inline-block text-sm font-sans text-primary km-interactive km-focus-visible km-inline-link underline decoration-primary/30 underline-offset-4 transition-colors"
              >
                Go to your dashboard →
              </Link>
            )}
            {actionStatus && (
              <div
                className={`text-sm font-sans ${isError ? 'text-negative' : 'text-primary/70'}`}
                role="status"
                aria-live="polite"
              >
                <p>{actionStatus}</p>
                {isError && (
                  <button
                    type="button"
                    onClick={handleImport}
                    className="mt-2 px-4 py-1.5 border border-primary/20 text-primary rounded-sm text-sm font-serif km-interactive km-focus-visible transition-all"
                  >
                    Retry
                  </button>
                )}
              </div>
            )}
          </div>
        )}

        {/* Importing phase */}
        {onboardingPhase === 'importing' && (
          <div className="max-w-lg flex items-center gap-4">
            <LoadingSpinner size="sm" label="Importing games" />
            <p className="text-lg font-sans text-primary/70">{actionStatus || 'Importing games...'}</p>
          </div>
        )}

        {/* Generating phase */}
        {onboardingPhase === 'generating' && (
          <div className="max-w-lg">
            {generationJob ? (
              <JobStatusCard
                status={generationJob.status}
                progress={generationJob.progress || 0}
                message={generationJob.message}
                error={generationJob.status === 'failed' ? (generationJob.error || generationJob.message) : undefined}
              />
            ) : (
              <div className="flex items-center gap-4">
                <LoadingSpinner size="sm" label="Starting puzzle generation" />
                <p className="text-lg font-sans text-primary/70">Starting puzzle generation...</p>
              </div>
            )}
          </div>
        )}
      </section>

      {/* ── Quick Stats (returning users with data) ── */}
      {hasData && userStatus && (
        <section
          className="flex flex-wrap gap-x-12 gap-y-4 border-t border-b border-primary/10 py-6"
          aria-label="Account stats"
        >
          <div>
            <p className="text-3xl font-mono text-primary">{userStatus.games_count}</p>
            <p className="text-sm font-sans text-primary/70">games imported</p>
          </div>
          <div>
            <p className="text-3xl font-mono text-primary">{userStatus.puzzles_count}</p>
            <p className="text-sm font-sans text-primary/70">puzzles generated</p>
          </div>
          {/* No due-count tile: that's live training state, which belongs to the
              Dashboard — Home's stats describe the imported data, not the queue. */}
        </section>
      )}

      {/* ── Action Cards (authenticated users) ── */}
      {!isNewUser && (
        <section className="grid grid-cols-1 md:grid-cols-3 gap-6" aria-labelledby="explore-heading">
          {/* Gives the card <h3>s a parent heading so levels don't jump h1→h3,
              and names this landmark region via aria-labelledby. */}
          <h2 id="explore-heading" className="sr-only">Explore KnightMind</h2>
          <Link
            to="/puzzles"
            className="group bg-primary/5 border border-primary/10 rounded-sm p-6 transition-all hover:border-primary/30 km-focus-visible block"
          >
            <h3 className="text-xl font-serif text-primary mb-2 group-hover:translate-x-1 transition-transform duration-300">
              Daily Puzzles
            </h3>
            {/* Static door copy — no live due counts here; the Dashboard owns
                the training queue. */}
            <p className="text-sm font-sans text-primary/70 mb-4">
              {hasPuzzles
                ? 'Solve puzzles generated from your own games'
                : 'Import games to generate puzzles'}
            </p>
            <span className="text-primary/70 group-hover:text-primary/70 transition-colors text-sm font-sans">
              Open →
            </span>
          </Link>

          <Link
            to="/dashboard"
            className="group bg-primary/5 border border-primary/10 rounded-sm p-6 transition-all hover:border-primary/30 km-focus-visible block"
          >
            <h3 className="text-xl font-serif text-primary mb-2 group-hover:translate-x-1 transition-transform duration-300">
              Dashboard
            </h3>
            <p className="text-sm font-sans text-primary/70 mb-4">
              {hasData
                ? 'Review your tactical radar, streaks, and trends'
                : 'Your training overview will appear here'}
            </p>
            <span className="text-primary/70 group-hover:text-primary/70 transition-colors text-sm font-sans">
              Open →
            </span>
          </Link>

          <Link
            to="/openings"
            className="group bg-primary/5 border border-primary/10 rounded-sm p-6 transition-all hover:border-primary/30 km-focus-visible block"
          >
            <h3 className="text-xl font-serif text-primary mb-2 group-hover:translate-x-1 transition-transform duration-300">
              Opening Explorer
            </h3>
            <p className="text-sm font-sans text-primary/70 mb-4">
              {hasData
                ? 'Explore your opening repertoire and trends'
                : 'Visualize your opening choices after import'}
            </p>
            <span className="text-primary/70 group-hover:text-primary/70 transition-colors text-sm font-sans">
              Open →
            </span>
          </Link>
        </section>
      )}

      {/* ── Value Prop (new users) ── */}
      {isNewUser && (
        <section className="border-t border-primary/10 pt-12">
          <div className="grid grid-cols-1 md:grid-cols-3 gap-8 text-center">
            <div>
              <p className="text-4xl mb-3" aria-hidden="true">&#9823;</p>
              <h2 className="font-serif text-primary text-lg mb-1">Import Games</h2>
              <p className="text-sm font-sans text-primary/70">
                Connect your Chess.com account to pull in your game history.
              </p>
            </div>
            <div>
              <p className="text-4xl mb-3" aria-hidden="true">&#9822;</p>
              <h2 className="font-serif text-primary text-lg mb-1">Generate Puzzles</h2>
              <p className="text-sm font-sans text-primary/70">
                Puzzles are created from your actual missed tactics.
              </p>
            </div>
            <div>
              <p className="text-4xl mb-3" aria-hidden="true">&#9818;</p>
              <h2 className="font-serif text-primary text-lg mb-1">Master Patterns</h2>
              <p className="text-sm font-sans text-primary/70">
                Spaced repetition ensures you remember what you learn.
              </p>
            </div>
          </div>
        </section>
      )}

      {/* Celebration modal on completion */}
      <Modal
        isOpen={onboardingPhase === 'complete'}
        onClose={() => navigate('/dashboard')}
        closeOnEscape={true}
        closeOnOverlayClick={true}
      >
        <div className="bg-bg-primary border border-positive-soft rounded-sm p-12 max-w-md text-center">
          <div className="flex justify-center mb-6">
            <div className="h-16 w-16 rounded-full bg-positive-fill flex items-center justify-center">
              <svg className="h-10 w-10 text-positive" fill="none" viewBox="0 0 24 24" stroke="currentColor" aria-hidden="true">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
              </svg>
            </div>
          </div>
          <h2 className="text-3xl font-serif text-primary mb-4">All Set!</h2>
          <p className="text-primary/70 font-sans mb-2">
            {generatedPuzzleCount !== null && generatedPuzzleCount > 0
              ? `${generatedPuzzleCount} puzzle${generatedPuzzleCount === 1 ? '' : 's'} generated from your games.`
              : 'Your puzzles are ready.'}
          </p>
          <p className="text-primary/70 text-sm font-sans">
            Taking you to your dashboard...
          </p>
        </div>
      </Modal>
    </div>
  );
}
