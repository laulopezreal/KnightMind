import { useEffect, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { importChessComGames, getImportStatus, validateChessComUser, ApiError } from '../api';
import { generatePuzzles } from '../api/puzzles';
import { useChessUsername } from '../context/ChessUsernameContext';
import { formatRelativeTime } from '../utils/time';
import { useJobPolling } from '../hooks/useJobPolling';
import { Modal } from '../components/Modal';
import { JobStatusCard } from '../components/JobStatusCard';


type ImportStatus = {
  lastImportedAt: string | null;
  lastNewGames: number | null;
};


type OnboardingPhase = 'idle' | 'importing' | 'generating' | 'complete';

export default function Home() {
  const { username } = useChessUsername();
  const navigate = useNavigate();
  const [status, setStatus] = useState<string | null>(null);
  const [isError, setIsError] = useState(false);
  const [loading, setLoading] = useState(false);
  const [importStatus, setImportStatus] = useState<ImportStatus>({
    lastImportedAt: null,
    lastNewGames: null,
  });
  const [statusLoading, setStatusLoading] = useState(false);
  const [statusError, setStatusError] = useState<string | null>(null);

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
      setStatus(`Puzzle generation failed: ${err.message}. You can generate them manually from the Puzzles page.`);
      setIsError(true);
      setOnboardingPhase('idle');
      setGeneratingJobId(null);
    }
  });

  useEffect(() => {
    let isActive = true;

    const fetchImportStatus = async () => {
      if (!username) {
        setImportStatus({ lastImportedAt: null, lastNewGames: null });
        setStatusError(null);
        return;
      }
      setStatusLoading(true);
      setStatusError(null);
      try {
        const response = await getImportStatus(username);
        if (!isActive) return;
        setImportStatus({
          lastImportedAt: response.last_imported_at,
          lastNewGames: response.last_new_games,
        });
      } catch (error) {
        if (!isActive) return;
        const message = error instanceof Error ? error.message : 'Unable to load sync details.';
        setStatusError(message);
      } finally {
        if (isActive) {
          setStatusLoading(false);
        }
      }
    };

    fetchImportStatus();

    return () => {
      isActive = false;
    };
  }, [username]);

  const handleImport = async () => {
    if (!username.trim()) {
      setStatus('Please enter a Chess.com username');
      setIsError(true);
      return;
    }

    // Validate username exists on Chess.com before importing
    setLoading(true);
    setStatus('Validating username...');
    setIsError(false);

    try {
      const validation = await validateChessComUser(username.trim());
      if (!validation.valid) {
        setStatus('Username not found on Chess.com. Please check spelling.');
        setIsError(true);
        setLoading(false);
        return;
      }
    } catch {
      setStatus('Could not validate username. Continuing with import...');
      // Continue anyway if validation fails
    }

    setStatus('Fetching games...');
    setOnboardingPhase('importing');

    try {
      const result = await importChessComGames(username);
      if (result.games_count === 0) {
        setStatus('No games found.');
        setOnboardingPhase('idle');
      } else if (result.new_games === 0) {
        setStatus(`No new games found. You have ${result.games_count} total games.`);
        setOnboardingPhase('idle');
      } else {
        // Automatically trigger puzzle generation for new games
        setNewGamesCount(result.new_games);
        setStatus(`Imported ${result.new_games} new games. Generating puzzles...`);
        setOnboardingPhase('generating');

        try {
          const jobResult = await generatePuzzles(username);
          setGeneratingJobId(jobResult.job_id);
        } catch {
          setStatus(
            `Imported ${result.new_games} new games, but puzzle generation failed. You can generate them manually from the Puzzles page.`
          );
          setIsError(true);
          setOnboardingPhase('idle');
        }
      }
      setImportStatus({
        lastImportedAt: new Date().toISOString(),
        lastNewGames: result.new_games,
      });
      setIsError(false);
    } catch (error) {
      if (error instanceof ApiError) {
        setStatus(error.detail || error.message);
      } else {
        setStatus(error instanceof Error ? error.message : 'Unknown error');
      }
      setIsError(true);
      setOnboardingPhase('idle');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="space-y-20 animate-teedin">
      <section className="space-y-6">
        <h1 className="text-6xl md:text-8xl font-serif text-primary tracking-tight">
          KnightMind
        </h1>
        <p className="text-xl md:text-2xl font-light text-primary/60 max-w-2xl leading-relaxed">
          Your personal chess intelligence platform. <br />
          Analyze your games, find patterns, and master your intuition.
        </p>
      </section>

      <section className="grid grid-cols-1 md:grid-cols-2 gap-16">
        <div className="space-y-8">
          <div className="prose">
            <h3 className="text-2xl font-serif text-primary">
              {username ? 'Welcome Back' : 'Begin Analysis'}
            </h3>
            <p className="font-sans text-primary/60">
              {username
                ? 'Have you played more? Sync to import your newest games, or jump straight into insights.'
                : 'Import your games from Chess.com to generate personalized puzzles and insights.'}
            </p>
          </div>

          <div className="space-y-4">
            <div className="flex gap-4 border-b border-primary/20 pb-2 focus-within:border-primary/80 transition-colors">
              {username ? (
                <div className="flex-1 flex items-center text-lg font-sans text-primary">
                  <span className="opacity-60 mr-2">Chess.com:</span>
                  <span className="font-medium">{username}</span>
                </div>
              ) : (
                <div className="flex-1 flex items-center text-lg font-sans text-primary/40 italic">
                  Set your Chess.com username to import games
                </div>
              )}
              <button
                type="button"
                onClick={handleImport}
                disabled={loading || !username}
                className={`text-primary font-medium transition-opacity uppercase tracking-widest text-sm km-focus-visible rounded-sm px-2 py-1 ${loading || !username ? 'km-interactive-disabled disabled:opacity-30' : 'km-interactive'}`}
              >
                {loading ? '...' : username ? 'Sync' : 'Import'}
              </button>
            </div>

            {username && (
              <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-sm font-sans text-primary/60">
                {statusLoading && <span className="animate-pulse">Loading sync details...</span>}
                {!statusLoading && statusError && (
                  <span className="text-red-500/80">{statusError}</span>
                )}
                {!statusLoading && !statusError && importStatus.lastImportedAt && (
                  <span>Last synced: {formatRelativeTime(importStatus.lastImportedAt)}</span>
                )}
                {!statusLoading && !statusError && (importStatus.lastNewGames ?? 0) > 0 && (
                  <Link
                    to="/puzzles"
                    className="text-primary hover:text-primary/80 transition-colors"
                  >
                    Generate puzzles from new games →
                  </Link>
                )}
              </div>
            )}

            {status && (
              <p className={`text-sm font-sans tracking-wide ${isError ? 'text-red-500/80' : 'text-primary/60'}`}>
                {status}
              </p>
            )}
          </div>
        </div>

        <div className="space-y-8">
          <h3 className="text-2xl font-serif text-primary">Explore</h3>
          <div className="flex flex-col gap-6 font-sans text-lg">
            <Link to="/dashboard" className="group flex items-center justify-between border-b border-primary/10 py-4 hover:border-primary/40 transition-colors">
              <span className="group-hover:translate-x-2 transition-transform duration-500">Dashboard</span>
              <span className="opacity-0 group-hover:opacity-100 transition-opacity">→</span>
            </Link>
            <Link to="/puzzles" className="group flex items-center justify-between border-b border-primary/10 py-4 hover:border-primary/40 transition-colors">
              <span className="group-hover:translate-x-2 transition-transform duration-500">Daily Puzzles</span>
              <span className="opacity-0 group-hover:opacity-100 transition-opacity">→</span>
            </Link>
            <Link to="/openings" className="group flex items-center justify-between border-b border-primary/10 py-4 hover:border-primary/40 transition-colors">
              <span className="group-hover:translate-x-2 transition-transform duration-500">Opening Explorer</span>
              <span className="opacity-0 group-hover:opacity-100 transition-opacity">→</span>
            </Link>
          </div>
        </div>
      </section>

      {/* Job status card during puzzle generation */}
      {onboardingPhase === 'generating' && generationJob && (
        <div className="mt-8">
          <JobStatusCard
            status={generationJob.status}
            progress={generationJob.progress || 0}
            message={generationJob.message}
            error={generationJob.status === 'failed' ? generationJob.message : undefined}
          />
        </div>
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
    </div>
  );
}
