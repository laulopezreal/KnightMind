import { useEffect, useState, useCallback } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { importChessComGames, getImportStatus, validateChessComUser, getUserStatus, ApiError } from '../api';
import type { UserStatus } from '../api';
import { generatePuzzles } from '../api/puzzles';
import { useChessUsername } from '../context/ChessUsernameContext';
import { formatRelativeTime } from '../utils/time';
import { useJobPolling } from '../hooks/useJobPolling';
import { Modal } from '../components/Modal';
import { JobStatusCard } from '../components/JobStatusCard';
import { LoadingSpinner } from '../components/LoadingSpinner';


type ImportStatus = {
  lastImportedAt: string | null;
  lastNewGames: number | null;
};


type OnboardingPhase = 'idle' | 'importing' | 'generating' | 'complete';

export default function Home() {
  const { username, setEditorOpen } = useChessUsername();
  const navigate = useNavigate();

  // Page data
  const [userStatus, setUserStatus] = useState<UserStatus | null>(null);
  const [pageLoading, setPageLoading] = useState(true);
  const [pageError, setPageError] = useState<string | null>(null);

  // Import states
  const [importStatus, setImportStatus] = useState<ImportStatus>({
    lastImportedAt: null,
    lastNewGames: null,
  });
  const [actionStatus, setActionStatus] = useState<string | null>(null);
  const [isError, setIsError] = useState(false);
  const [loading, setLoading] = useState(false);

  // Onboarding state
  const [onboardingPhase, setOnboardingPhase] = useState<OnboardingPhase>('idle');
  const [generatingJobId, setGeneratingJobId] = useState<string | null>(null);
  const [newGamesCount, setNewGamesCount] = useState<number>(0);

  // Job polling for puzzle generation
  const { job: generationJob } = useJobPolling(generatingJobId, {
    enabled: onboardingPhase === 'generating',
    onSuccess: () => {
      setOnboardingPhase('complete');
      // Show celebration for 3 seconds, then redirect
      setTimeout(() => navigate('/dashboard'), 3000);
    },
    onError: (err) => {
      setActionStatus(`Puzzle generation failed: ${err.message}. You can generate them manually from the Puzzles page.`);
      setIsError(true);
      setOnboardingPhase('idle');
      setGeneratingJobId(null);
    }
  });

  // Fetch all page data on mount
  const loadPageData = useCallback(async () => {
    if (!username) {
      setPageLoading(false);
      return;
    }

    setPageLoading(true);
    setPageError(null);

    try {
      const [statusResult, importResult] = await Promise.allSettled([
        getUserStatus(username),
        getImportStatus(username),
      ]);

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
        setPageError('Unable to load your data. Please check your connection.');
      }
    } catch {
      setPageError('Unable to load your data. Please check your connection.');
    } finally {
      setPageLoading(false);
    }
  }, [username]);

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
        setNewGamesCount(result.new_games);
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
        setActionStatus(error.detail || error.message);
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
        <section className="space-y-6">
          <div className="h-16 w-64 bg-primary/10 rounded-sm animate-pulse" />
          <div className="h-6 w-96 max-w-full bg-primary/5 rounded-sm animate-pulse" />
        </section>
        <div className="h-14 w-56 bg-primary/10 rounded-sm animate-pulse" />
        <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
          {[1, 2, 3].map(i => (
            <div key={i} className="h-36 bg-primary/5 border border-primary/10 rounded-sm animate-pulse" />
          ))}
        </div>
      </div>
    );
  }

  // ── Error State (full-page, when no data loaded) ──
  if (pageError && !userStatus) {
    return (
      <div className="space-y-12 animate-teedin">
        <section className="space-y-6">
          <h1 className="text-6xl md:text-8xl font-serif text-primary tracking-tight">
            KnightMind
          </h1>
          <p className="text-xl font-light text-primary/60 max-w-2xl leading-relaxed">
            Your personal chess intelligence platform.
          </p>
        </section>
        <section className="bg-red-500/5 border border-red-500/20 rounded-sm p-8 max-w-lg">
          <h3 className="text-xl font-serif text-primary mb-3">Unable to Load</h3>
          <p className="text-primary/60 font-sans mb-6">{pageError}</p>
          <button
            type="button"
            onClick={loadPageData}
            className="px-6 py-3 bg-primary text-bg-primary rounded-sm font-serif km-interactive km-focus-visible"
          >
            Retry
          </button>
        </section>
      </div>
    );
  }

  return (
    <main className="space-y-16 animate-teedin">
      {/* ── Hero Section ── */}
      <section className="space-y-6">
        <h1 className="text-6xl md:text-8xl font-serif text-primary tracking-tight">
          KnightMind
        </h1>
        <p className="text-xl md:text-2xl font-light text-primary/60 max-w-2xl leading-relaxed">
          {isNewUser
            ? 'Your personal chess intelligence platform. Connect your Chess.com account to begin.'
            : hasPuzzles && userStatus!.due_count > 0
              ? `${userStatus!.due_count} puzzles are waiting for you, ${username}.`
              : hasData
                ? `Welcome back, ${username}. Pick up where you left off.`
                : `Connected as ${username}. Import your games to generate personalized puzzles.`
          }
        </p>
      </section>

      {/* ── Primary CTA Section ── */}
      <section aria-label="Primary action">
        {/* No username → connect account */}
        {isNewUser && (
          <div className="space-y-4 max-w-lg">
            <button
              type="button"
              onClick={() => setEditorOpen(true)}
              className="px-8 py-4 bg-primary text-bg-primary rounded-sm text-lg font-serif km-interactive km-focus-visible transition-colors"
            >
              Connect Chess.com Account
            </button>
            <p className="text-sm font-sans text-primary/40">
              Import your games and generate puzzles from your real positions.
            </p>
          </div>
        )}

        {/* Has username, idle → context-dependent CTA */}
        {!isNewUser && onboardingPhase === 'idle' && (
          <div className="space-y-4 max-w-lg">
            {hasPuzzles && userStatus!.due_count > 0 ? (
              /* Due puzzles exist → training is primary, sync is secondary */
              <div className="space-y-4">
                <Link
                  to="/puzzles"
                  className="inline-block px-8 py-4 bg-primary text-bg-primary rounded-sm text-lg font-serif km-interactive km-focus-visible transition-colors"
                >
                  Start Training
                </Link>
                <div className="flex items-center gap-4 flex-wrap">
                  <button
                    type="button"
                    onClick={handleImport}
                    disabled={loading}
                    className={`text-sm font-sans text-primary/50 underline km-focus-visible ${
                      loading ? 'km-interactive-disabled' : 'km-interactive hover:text-primary'
                    }`}
                  >
                    {loading ? 'Syncing...' : 'Sync new games'}
                  </button>
                  {importStatus.lastImportedAt && (
                    <span className="text-sm font-sans text-primary/40">
                      Last synced {formatRelativeTime(importStatus.lastImportedAt)}
                    </span>
                  )}
                </div>
              </div>
            ) : (
              /* No due puzzles or no data → import/sync is primary */
              <div className="flex items-center gap-4 flex-wrap">
                <button
                  type="button"
                  onClick={handleImport}
                  disabled={loading}
                  className={`px-8 py-4 bg-primary text-bg-primary rounded-sm text-lg font-serif transition-colors km-focus-visible ${
                    loading ? 'km-interactive-disabled disabled:opacity-50' : 'km-interactive'
                  }`}
                >
                  {loading ? 'Syncing...' : hasData ? 'Sync New Games' : 'Import Games'}
                </button>
                {importStatus.lastImportedAt && (
                  <span className="text-sm font-sans text-primary/40">
                    Last synced {formatRelativeTime(importStatus.lastImportedAt)}
                  </span>
                )}
              </div>
            )}
            {actionStatus && (
              <div
                className={`text-sm font-sans ${isError ? 'text-red-500/80' : 'text-primary/60'}`}
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
            <p className="text-lg font-sans text-primary/60">{actionStatus || 'Importing games...'}</p>
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
                error={generationJob.status === 'failed' ? generationJob.message : undefined}
              />
            ) : (
              <div className="flex items-center gap-4">
                <LoadingSpinner size="sm" label="Starting puzzle generation" />
                <p className="text-lg font-sans text-primary/60">Starting puzzle generation...</p>
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
            <p className="text-sm font-sans text-primary/40">games imported</p>
          </div>
          <div>
            <p className="text-3xl font-mono text-primary">{userStatus.puzzles_count}</p>
            <p className="text-sm font-sans text-primary/40">puzzles generated</p>
          </div>
          {userStatus.due_count > 0 && (
            <div>
              <p className="text-3xl font-mono text-primary">{userStatus.due_count}</p>
              <p className="text-sm font-sans text-primary/40">puzzles due</p>
            </div>
          )}
        </section>
      )}

      {/* ── Action Cards (authenticated users) ── */}
      {!isNewUser && (
        <section className="grid grid-cols-1 md:grid-cols-3 gap-6" aria-label="Navigation">
          <Link
            to="/puzzles"
            className="group bg-primary/5 border border-primary/10 rounded-sm p-6 transition-all hover:border-primary/30 km-focus-visible block"
          >
            <h3 className="text-xl font-serif text-primary mb-2 group-hover:translate-x-1 transition-transform duration-300">
              Daily Puzzles
            </h3>
            <p className="text-sm font-sans text-primary/50 mb-4">
              {hasPuzzles && userStatus
                ? userStatus.due_count > 0
                  ? `${userStatus.due_count} puzzles ready for review`
                  : 'All caught up — practice more anytime'
                : 'Import games to generate puzzles'}
            </p>
            <span className="text-primary/30 group-hover:text-primary/60 transition-colors text-sm font-sans">
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
            <p className="text-sm font-sans text-primary/50 mb-4">
              {hasData
                ? 'Review your tactical radar, streaks, and trends'
                : 'Your training overview will appear here'}
            </p>
            <span className="text-primary/30 group-hover:text-primary/60 transition-colors text-sm font-sans">
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
            <p className="text-sm font-sans text-primary/50 mb-4">
              {hasData
                ? 'Explore your opening repertoire and trends'
                : 'Visualize your opening choices after import'}
            </p>
            <span className="text-primary/30 group-hover:text-primary/60 transition-colors text-sm font-sans">
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
              <h4 className="font-serif text-primary text-lg mb-1">Import Games</h4>
              <p className="text-sm font-sans text-primary/40">
                Connect your Chess.com account to pull in your game history.
              </p>
            </div>
            <div>
              <p className="text-4xl mb-3" aria-hidden="true">&#9822;</p>
              <h4 className="font-serif text-primary text-lg mb-1">Generate Puzzles</h4>
              <p className="text-sm font-sans text-primary/40">
                Puzzles are created from your actual missed tactics.
              </p>
            </div>
            <div>
              <p className="text-4xl mb-3" aria-hidden="true">&#9818;</p>
              <h4 className="font-serif text-primary text-lg mb-1">Master Patterns</h4>
              <p className="text-sm font-sans text-primary/40">
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
        <div className="bg-primary border border-green-500/30 rounded-sm p-12 max-w-md text-center">
          <div className="flex justify-center mb-6">
            <div className="h-16 w-16 rounded-full bg-green-500 flex items-center justify-center">
              <svg className="h-10 w-10 text-white" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
              </svg>
            </div>
          </div>
          <h2 className="text-3xl font-serif text-primary mb-4">All Set!</h2>
          <p className="text-primary/60 font-sans mb-2">
            {newGamesCount} puzzles generated from your games.
          </p>
          <p className="text-primary/40 text-sm font-sans">
            Taking you to your dashboard...
          </p>
        </div>
      </Modal>
    </main>
  );
}
