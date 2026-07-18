import { useEffect, useRef, useState } from 'react';
import { Link, useSearchParams, useNavigate } from 'react-router-dom';
import { Chessboard } from 'react-chessboard';
import { Chess } from 'chess.js';
import { generatePuzzles, getDailyPuzzles, cancelJob, ApiError } from '../api';
import { JobStatusCard } from '../components/JobStatusCard';
import { SessionSummaryCard } from '../components/SessionSummaryCard';
import { WarmupSummary } from '../components/WarmupSummary';
import { AchievementsList } from '../components/AchievementsList';
import { RecentSessionsCard } from '../components/RecentSessionsCard';
import { useJobPolling } from '../hooks/useJobPolling';
import { useChessUsername } from '../context/ChessUsernameContext';
import { usePuzzleMode } from '../context/PuzzleModeContext';
import { useClue } from '../hooks/useClue';
import { usePuzzleTimer } from '../hooks/usePuzzleTimer';
import { useAchievements } from '../hooks/useAchievements';
import { usePuzzleInsights } from '../hooks/usePuzzleInsights';
import { usePuzzleSession, type PuzzleStatus } from '../hooks/usePuzzleSession';
import { getModeLabels, getPuzzleActionA11yCopy, getSessionDetailsA11yCopy } from '../utils/a11yCopy';

export default function Puzzles() {
    const { username, setEditorOpen } = useChessUsername();
    const { sessionType, targetAccuracy, setTargetAccuracy, targetTimeMinutes, setTargetTimeMinutes } = usePuzzleMode();
    const navigate = useNavigate();
    const [userMove, setUserMove] = useState('');
    const [status, setStatus] = useState<PuzzleStatus>('solving');
    const [showUciInput, setShowUciInput] = useState(false);
    const [activeJobId, setActiveJobId] = useState<string | null>(() => {
        if (!username) return null;
        return localStorage.getItem(`knightmind:lastJob:${username}`);
    });
    const [prevUsername, setPrevUsername] = useState(username);
    const [game, setGame] = useState(new Chess());

    // Get motif filter and warmup mode from URL query params
    const [searchParams] = useSearchParams();
    const motifFilter = searchParams.get('motif');
    const isWarmupMode = searchParams.get('warmup') === 'true';

    // Warmup state
    const [warmupMode, setWarmupMode] = useState(isWarmupMode);
    const [showSessionDetails, setShowSessionDetails] = useState(false);

    // Shared state: activeSessionId is needed by both timer and session hooks
    const [activeSessionId, setActiveSessionId] = useState<string | null>(null);

    const { achievements, checkAchievements, checkSessionAchievements } = useAchievements(username);
    const insights = usePuzzleInsights(username);
    const {
        userStatus, isLoadingStatus, motifPerformance, recentSessions,
        insightsError, isRefreshingInsights,
        refreshUserStatus, refreshRecentSessions, refreshMotifPerformance,
        handleRefreshInsights,
    } = insights;

    const statusRef = useRef(status);
    const [previousSessionId, setPreviousSessionId] = useState<string | null>(null);
    useEffect(() => {
        statusRef.current = status;
    }, [status]);

    // Keep advanced stats collapsed when a new session starts.
    if (activeSessionId !== previousSessionId) {
        setPreviousSessionId(activeSessionId);
        if (activeSessionId) {
            setShowSessionDetails(false);
        }
    }

    const handleReviewPuzzleRef = useRef<((result: 'pass' | 'fail', timeMs?: number) => Promise<void>)>(async () => { });
    const isAdvancingPuzzle = useRef(false);

    const timer = usePuzzleTimer({
        sessionType,
        activeSessionId,
        onPuzzleTimeout: () => {
            if (statusRef.current === 'solving') {
                handleReviewPuzzleRef.current('fail');
                setStatus('incorrect');
            }
        },
    });

    const session = usePuzzleSession({
        activeSessionId,
        setActiveSessionId,
        setStatus,
        username,
        sessionType,
        targetAccuracy,
        targetTimeMinutes,
        warmupMode,
        motifFilter,
        userStatus,
        timer,
        checkAchievements,
        checkSessionAchievements,
        refreshRecentSessions,
        refreshMotifPerformance,
        refreshUserStatus,
    });

    const {
        sessionState, sessionSummary, isResumingSession,
        streak, bestStreak, hintsUsed, reviewedCount, performanceHistory,
        puzzles, currentIndex, isLoading, error, lastFeedback,
        setPuzzles, setCurrentIndex, setError, setLastFeedback,
        setSessionSummary, setSessionState, setReviewedCount,
        handleStartSession, handleReviewPuzzle, handleCompleteSession, handleUseHint,
        calculateRecentPerformance, getPerformanceTrend,
    } = session;

    const startPuzzleTimer = timer.startPuzzleTimer;
    const currentPuzzle = puzzles[currentIndex];
    const clue = useClue(currentPuzzle?.best_move_uci ?? '', currentPuzzle?.fen ?? '');
    const clueReset = clue.reset;
    const puzzlesAvailable = puzzles.length > 0;
    const isFinalPuzzle = puzzlesAvailable && currentIndex >= puzzles.length - 1;
    // Disable only while the completion API call is in-flight. Once completed,
    // render a real post-session action instead of a dead final-puzzle CTA.
    const finishButtonDisabled = sessionState === 'completing';
    const controlsEnabled = sessionState === 'idle' || sessionState === 'error';

    // Sync activeJobId when username changes (during render, not in effect)
    if (prevUsername !== username) {
        setPrevUsername(username);
        const savedJobId = username
            ? localStorage.getItem(`knightmind:lastJob:${username}`)
            : null;
        setActiveJobId(savedJobId);
    }

    const { job, isPolling: isJobPolling } = useJobPolling(activeJobId, {
        enabled: !!activeJobId,
        onSuccess: async () => {
            try {
                const res = await getDailyPuzzles(username, 5);
                setPuzzles(res.puzzles);
                setCurrentIndex(0);
                setStatus('solving');
                setUserMove('');
                if (res.puzzles.length > 0) {
                    setSessionState('active');
                    setError(null);
                } else {
                    setSessionState('error');
                    setError('No puzzles returned from generation');
                }
            } catch (err) {
                console.error('Failed to refresh puzzles after generation:', err);
                const message = err instanceof Error ? err.message : 'Failed to refresh puzzles';
                setError(message);
            }

            localStorage.removeItem(`knightmind:lastJob:${username}`);

            // Refresh user status to update has_new_games flag
            await refreshUserStatus();
        },
        onError: (err) => {
            localStorage.removeItem(`knightmind:lastJob:${username}`);
            const message = err instanceof Error ? err.message : 'Failed to generate puzzles';
            setSessionState('error');
            setError(message);
        }
    });

    const isGenerating = isJobPolling || (job?.status === 'queued' || job?.status === 'running');
    const controlsDisabled = !controlsEnabled || isLoading || isGenerating;
    const generateNewDisabled = !controlsEnabled || isLoading || isGenerating || !userStatus?.has_new_games;
    const { selectedModeLabel, screenReaderModeLabel } = getModeLabels(sessionType);
    const modeAvailabilityLabel = sessionType === 'standard' ? 'Active' : 'Beta';
    const startSessionDisabledReason = !username
        ? 'Set your username first to start training.'
        // A session is running (the Start button is hidden), so there is nothing
        // "loading or generating" to wait on — don't show a start reason at all.
        : activeSessionId
            ? null
            : controlsDisabled
                ? 'Please wait for the current task to finish.'
                : !userStatus
                ? ((insightsError && !isLoadingStatus && !isRefreshingInsights) ? "Couldn't load your training data." : 'Loading your training data...')
                : userStatus.puzzles_count === 0
                    ? 'Generate puzzles first to unlock sessions.'
                    : userStatus.due_count === 0
                        ? 'No puzzles are due right now. Generate new puzzles to keep training.'
                        : sessionType !== 'standard'
                            ? 'Only Standard mode can start sessions for now. Switch mode in the sidebar.'
                            : null;
    const generateDisabledReason = !username
        ? 'Set your username first to generate puzzles.'
        : isGenerating
            ? 'Puzzle generation is already in progress.'
            : activeSessionId
                ? 'Finish your current session before generating new puzzles.'
                : !controlsEnabled || isLoading
                    ? 'Please finish the current flow before generating more puzzles.'
                : !userStatus?.has_new_games
                    ? !userStatus
                        ? ((insightsError && !isLoadingStatus && !isRefreshingInsights) ? "Couldn't load your training data." : 'Loading your training data...')
                        : userStatus.games_count === 0
                            ? 'No games imported yet. Sync games from Chess.com to get started.'
                            : userStatus.due_count > 0
                                ? `All imported games are already processed. Train your ${userStatus.due_count} due puzzle${userStatus.due_count === 1 ? '' : 's'}, or sync newer games from Chess.com.`
                                : 'All imported games are already processed. Sync newer games from Chess.com to generate more puzzles.'
                    : null;
    const generateButtonLabel = isGenerating
        ? 'Generating...'
        : userStatus && !userStatus.has_new_games && userStatus.games_count > 0
            ? 'No new games to generate'
            : 'Generate New';
    const sessionDetailsA11yCopy = getSessionDetailsA11yCopy(showSessionDetails, screenReaderModeLabel);
    const puzzleActionA11yCopy = getPuzzleActionA11yCopy(activeSessionId, hintsUsed);

    const handleGeneratePuzzles = async () => {
        if (!username.trim()) {
            setError('Please enter a username');
            return;
        }
        setSessionState('loading');
        setError(null);

        try {
            const { job_id } = await generatePuzzles(username.trim());
            setActiveJobId(job_id);
            localStorage.setItem(`knightmind:lastJob:${username.trim()}`, job_id);
            // Polling will auto-start
        } catch (err) {
            if (err instanceof ApiError) {
                if (err.statusCode === 404) {
                    // Differentiate between no games at all vs no new games
                    if (userStatus?.games_count === 0) {
                        setError('No games found. Please import games first.');
                    } else {
                        setError('No new games available. All current games have been used for puzzles. Import more games to generate new puzzles.');
                    }
                } else {
                    // Show the friendly message; keep the raw cause (endpoints,
                    // timeouts, "backend is down") in the console for devs only.
                    if (err.detail) console.error('[puzzles:generate]', err.detail);
                    setError(err.message);
                }
            } else {
                setError(err instanceof Error ? err.message : 'Failed to generate puzzles');
            }
            setSessionState('error');
        }
    };

    const handleCancelJob = async () => {
        if (!activeJobId) return;

        try {
            await cancelJob(activeJobId);
            // Job status will be updated via polling
        } catch (err) {
            console.error('Failed to cancel job:', err);
            setError(err instanceof Error ? err.message : 'Failed to cancel job');
        }
    };

    // Keep ref in sync for timer timeout callback
    useEffect(() => {
        handleReviewPuzzleRef.current = handleReviewPuzzle;
    }, [handleReviewPuzzle]);

    // Auto-start warmup session when in warmup mode
    useEffect(() => {
        if (warmupMode && sessionState === 'idle' && username && userStatus && !isResumingSession) {
            handleStartSession();
        }
    }, [warmupMode, sessionState, username, userStatus, isResumingSession, handleStartSession]);

    const shouldShowJobStatusCard =
        !!job &&
        (job.status === 'queued' ||
            job.status === 'running' ||
            (!puzzlesAvailable && (job.status === 'succeeded' || job.status === 'failed')));
    const shouldShowErrorCard = sessionState === 'error' && !!error;
    const shouldShowLoadingCard =
        (isLoading || isLoadingStatus || isResumingSession) && !isGenerating && !shouldShowJobStatusCard;
    const shouldShowEmptyState =
        !isLoading &&
        !isGenerating &&
        !isLoadingStatus &&
        !puzzlesAvailable &&
        !shouldShowJobStatusCard &&
        !error;
    const shouldShowPartialDataCard =
        !!username &&
        !isLoadingStatus &&
        !!userStatus &&
        userStatus.puzzles_count > 0 &&
        (!motifPerformance || !!insightsError);
    const canRetryLoad = !!username && !isGenerating && !isLoading;
    // Status fetch failed (not merely still loading): userStatus is null, nothing
    // is loading/resuming/refreshing, no puzzles are already loaded, and an error
    // was recorded. Surfaces an error card instead of the misleading "Ready to
    // train" / "Loading your training data..." states — and never stacks on top
    // of a loading card or a working board.
    const statusLoadFailed =
        !!username &&
        !isLoadingStatus &&
        !isLoading &&
        !isResumingSession &&
        !userStatus &&
        !puzzlesAvailable &&
        // Stay true while a retry is in flight so the error card keeps its place
        // and its "Retrying..." button state shows, rather than unmounting.
        (!!insightsError || isRefreshingInsights) &&
        !isGenerating &&
        !shouldShowJobStatusCard &&
        !shouldShowErrorCard;

    const handleCheckAnswer = () => {
        if (!currentPuzzle) return;
        const normalizedUserMove = userMove.trim().toLowerCase();
        const normalizedBestMove = currentPuzzle.best_move_uci.toLowerCase();
        if (normalizedUserMove === normalizedBestMove) setStatus('correct');
        else setStatus('incorrect');
    };

    const handleRevealSolution = () => {
        setStatus('revealed');
        const bestMove = currentPuzzle?.best_move_uci?.toLowerCase();
        setUserMove(bestMove || '');
        if (currentPuzzle && bestMove) {
            const solutionGame = new Chess(currentPuzzle.fen);
            const from = bestMove.slice(0, 2);
            const to = bestMove.slice(2, 4);
            const promotion = bestMove.slice(4, 5);
            solutionGame.move({ from, to, promotion: promotion || undefined });
            setGame(solutionGame);
        }
    };

    const handleClue = () => {
        if (clue.clueStage === 1) {
            clue.advance();
            handleRevealSolution();
        } else {
            clue.advance();
        }
    };

    // Sync game board when puzzle changes (setState during render, not in effect)
    const [prevPuzzle, setPrevPuzzle] = useState(currentPuzzle);
    if (currentPuzzle && currentPuzzle !== prevPuzzle) {
        setPrevPuzzle(currentPuzzle);
        setGame(new Chess(currentPuzzle.fen));
    }

    // Reset clue and start timer when puzzle changes (side effects in effect)
    useEffect(() => {
        if (currentPuzzle) {
            clueReset();
            startPuzzleTimer();
        }
    }, [currentPuzzle, clueReset, startPuzzleTimer]);

    const onPieceDrop = (sourceSquare: string, targetSquare: string) => {
        if (!currentPuzzle || status === 'correct' || status === 'revealed') return false;
        try {
            const move = game.move({ from: sourceSquare, to: targetSquare, promotion: 'q' });
            if (move === null) return false;
            clue.reset();
            setGame(new Chess(game.fen()));
            const uciMove = `${move.from}${move.to}${move.promotion || ''}`;
            setUserMove(uciMove);
            const normalizedBestMove = currentPuzzle.best_move_uci.toLowerCase();
            if (uciMove === normalizedBestMove) setStatus('correct');
            else setStatus('incorrect');
            return true;
        } catch { return false; }
    };

    const handleNextPuzzle = () => {
        if (currentIndex < puzzles.length - 1) {
            setCurrentIndex(currentIndex + 1);
            setStatus('solving');
            setUserMove('');
            setLastFeedback('');
            clue.reset();
        }
    };

    const handleAdvancePuzzle = async () => {
        if (sessionState === 'completing' || sessionState === 'completed') return;
        if (isAdvancingPuzzle.current) return;

        isAdvancingPuzzle.current = true;
        try {
            if (status === 'correct') {
                await handleReviewPuzzle('pass');
            } else if (status === 'revealed') {
                // If solution was revealed, mark as fail before completing
                await handleReviewPuzzle('fail');
            }

            // One step of progress per puzzle finished — so retries (mark-failed
            // / reveal) never advance or complete the session early. Complete
            // only after the last puzzle is done.
            setReviewedCount(prev => prev + 1);
            if (!isFinalPuzzle) {
                handleNextPuzzle();
            } else {
                await handleCompleteSession();
            }
        } finally {
            isAdvancingPuzzle.current = false;
        }
    };

    return (
        <div className="space-y-12 animate-teedin">
            <section>
                <Link to="/dashboard" className="text-primary/40 hover:text-primary mb-4 inline-block font-sans text-sm tracking-widest uppercase transition-colors">
                    ← Back to Dashboard
                </Link>
                <div className="flex justify-between items-end">
                    <div>
                        <h1 className="text-4xl md:text-5xl font-serif text-primary mb-2">
                            {motifFilter ? `${motifFilter} Puzzles` : 'Daily Puzzles'}
                        </h1>
                        <div className="flex items-center gap-2 mb-3">
                            <span className="text-xs font-sans uppercase tracking-wider px-2 py-1 rounded-sm border border-primary/20 bg-primary/5 text-primary/80">
                                {selectedModeLabel} {modeAvailabilityLabel}
                            </span>
                            {sessionType !== 'standard' && (
                                <span className="text-xs font-sans text-primary/50">Switch to Standard to start sessions.</span>
                            )}
                        </div>
                        <p className="text-lg text-primary/60 font-sans">
                            {motifFilter ? `Practice ${motifFilter} tactical patterns` : 'Tactical patterns from your own games.'}
                        </p>
                    </div>
                </div>
            </section>

            {/* Parents the status/session <h3>s so heading levels don't jump h1→h3. */}
            <h2 className="sr-only">Your training</h2>

            {/* Controls */}
            <section className="bg-primary/5 border border-primary/10 rounded-sm p-6 backdrop-blur-sm space-y-6">
                {/* Top row: Username and buttons */}
                <div className="flex flex-col md:flex-row gap-6 items-end">
                    <div className="flex-1 relative min-w-[300px]">
                        {!username ? (
                            <div className="h-full flex items-center">
                                <span className="text-primary/60 font-sans mr-2">Set your Chess.com username to continue.</span>
                                <button
                                    type="button"
                                    onClick={() => setEditorOpen(true)}
                                    className="km-interactive km-focus-visible km-inline-link text-primary"
                                >
                                    Set Username
                                </button>
                            </div>
                        ) : (
                            <div className="text-xl font-serif text-primary py-2 border-b border-primary/20">
                                {username}
                            </div>
                        )}
                    </div>
                    <div className="flex gap-4 flex-wrap">
                        {!activeSessionId && (
                            <button
                                type="button"
                                onClick={handleStartSession}
                                disabled={controlsDisabled || !userStatus || userStatus.puzzles_count === 0 || userStatus.due_count === 0 || sessionType !== 'standard'}
                                title={startSessionDisabledReason ?? 'Start a new training session'}
                                className={`px-6 py-2 bg-primary text-bg-primary rounded-sm font-serif transition-opacity km-focus-visible ${(controlsDisabled || !userStatus || userStatus.puzzles_count === 0 || userStatus.due_count === 0 || sessionType !== 'standard') ? 'km-interactive-disabled disabled:opacity-50' : 'hover:opacity-90 cursor-pointer'}`}>
                                Start Session
                            </button>
                        )}
                        <button
                            type="button"
                            onClick={handleGeneratePuzzles}
                            disabled={generateNewDisabled}
                            title={generateDisabledReason ?? 'Generate puzzles from new games'}
                            className={`px-6 py-2 bg-primary text-bg-primary rounded-sm font-serif transition-colors km-focus-visible ${generateNewDisabled ? 'km-interactive-disabled disabled:opacity-50' : 'km-interactive'}`}>
                            {generateButtonLabel}
                        </button>
                    </div>
                </div>

                {(startSessionDisabledReason || generateDisabledReason) && (
                    <p className="text-sm text-primary/60 font-sans" role="status" aria-live="polite">
                        {startSessionDisabledReason ?? generateDisabledReason}
                    </p>
                )}

                {/* User status - full width below buttons */}
                {username && userStatus && !isLoadingStatus && (
                    <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-sm font-sans text-primary/60">
                        <span>Games: {userStatus.games_count}</span>
                        <span>Puzzles: {userStatus.puzzles_count}</span>
                        {userStatus.has_new_games ? (
                            <span className="text-green-600">
                                ✓ New games available for puzzles
                            </span>
                        ) : userStatus.games_count > 0 ? (
                            <span className="text-primary/40">
                                All games used for puzzles
                            </span>
                        ) : null}
                    </div>
                )}

                {/* Mode Information Cards - full width below user status */}
                {!activeSessionId && (
                    <>
                        {sessionType === 'standard' ? (
                            <div className="p-4 bg-primary/5 border border-primary/20 rounded-sm">
                                <p className="text-sm text-primary/60 font-sans">
                                    <strong className="font-medium">Standard mode</strong> uses spaced repetition to help you master tactical patterns from your own games.
                                    Complete 5 puzzles per session with immediate feedback on each move.
                                </p>
                            </div>
                        ) : (
                            <div className="p-4 bg-primary/5 border border-primary/20 rounded-sm">
                                <p className="text-sm text-primary/60 font-sans mb-3">
                                    🚧 <strong className="font-medium">{sessionType === 'timed' ? 'Timed' : 'Accuracy Goal'} mode</strong> is currently in development.
                                    Try it out by adjusting the settings, but sessions can only be started in Standard mode for now.
                                </p>
                                {sessionType === 'timed' && (
                                    <div className="flex items-center gap-2">
                                        <label htmlFor="duration-input" className="text-sm text-primary/60 font-sans">Duration:</label>
                                        <input
                                            id="duration-input"
                                            type="number"
                                            min="1"
                                            max="60"
                                            value={targetTimeMinutes}
                                            onChange={(e) => setTargetTimeMinutes(Number(e.target.value))}
                                            className="px-3 py-2 border border-primary/20 rounded-sm bg-bg-primary text-primary w-20"
                                        />
                                        <span className="text-sm text-primary/60 font-sans">minutes</span>
                                    </div>
                                )}
                                {sessionType === 'accuracy_goal' && (
                                    <div className="flex items-center gap-2">
                                        <label htmlFor="accuracy-input" className="text-sm text-primary/60 font-sans">Target accuracy:</label>
                                        <input
                                            id="accuracy-input"
                                            type="number"
                                            min="50"
                                            max="100"
                                            value={targetAccuracy}
                                            onChange={(e) => setTargetAccuracy(Number(e.target.value))}
                                            className="px-3 py-2 border border-primary/20 rounded-sm bg-bg-primary text-primary w-20"
                                        />
                                        <span className="text-sm text-primary/60 font-sans">%</span>
                                    </div>
                                )}
                            </div>
                        )}
                    </>
                )}

                {/* Job Status / Error Area */}
                <div className="mt-6">
                    {shouldShowEmptyState && userStatus && (
                        <div className="bg-primary/5 border border-primary/10 rounded-sm p-6 backdrop-blur-sm text-center space-y-4">
                            {userStatus.games_count === 0 ? (
                                <>
                                    <h3 className="font-serif text-xl text-primary">No games imported yet</h3>
                                    <p className="text-primary/60 font-sans">
                                        Import your Chess.com games to generate personalized puzzles.
                                    </p>
                                    <Link
                                        to="/"
                                        className="inline-block px-6 py-2 bg-primary text-bg-primary rounded-sm font-serif transition-colors km-interactive km-focus-visible"
                                    >
                                        Go to Home
                                    </Link>
                                </>
                            ) : userStatus.puzzles_count === 0 ? (
                                <>
                                    <h3 className="font-serif text-xl text-primary">Ready to generate puzzles</h3>
                                    <p className="text-primary/60 font-sans">
                                        We found {userStatus.games_count} games. Let&apos;s create training puzzles.
                                    </p>
                                    <button
                                        type="button"
                                        onClick={handleGeneratePuzzles}
                                        disabled={controlsDisabled}
                                        className={`px-6 py-2 bg-primary text-bg-primary rounded-sm font-serif transition-colors km-focus-visible ${controlsDisabled ? 'km-interactive-disabled disabled:opacity-50' : 'km-interactive'}`}
                                    >
                                        Generate Puzzles
                                    </button>
                                </>
                            ) : userStatus.due_count === 0 ? (
                                <>
                                    <h3 className="font-serif text-xl text-primary">All caught up</h3>
                                    <p className="text-primary/60 font-sans">
                                        {userStatus.next_due_at
                                            ? `Next review on ${new Date(userStatus.next_due_at).toLocaleDateString(undefined, { weekday: 'long', month: 'short', day: 'numeric' })}.`
                                            : 'No puzzles are due for review yet.'}
                                    </p>
                                    {userStatus.has_new_games && (
                                        <button
                                            type="button"
                                            onClick={handleGeneratePuzzles}
                                            disabled={controlsDisabled}
                                            className={`px-6 py-2 bg-primary text-bg-primary rounded-sm font-serif transition-colors km-focus-visible ${controlsDisabled ? 'km-interactive-disabled disabled:opacity-50' : 'km-interactive'}`}
                                        >
                                            Generate from New Games
                                        </button>
                                    )}
                                </>
                            ) : (
                                <>
                                    <h3 className="font-serif text-xl text-primary">
                                        {userStatus.due_count} puzzle{userStatus.due_count === 1 ? '' : 's'} ready
                                    </h3>
                                    <p className="text-primary/60 font-sans">
                                        Start a session to review your due puzzles.
                                    </p>
                                </>
                            )}
                        </div>
                    )}
                    {shouldShowEmptyState && isLoadingStatus && (
                        <div className="text-center text-primary/40 py-4">
                            <span className="animate-pulse">Loading training status...</span>
                        </div>
                    )}
                    {shouldShowEmptyState && !userStatus && !isLoadingStatus && !insightsError && !isRefreshingInsights && (
                        <div className="bg-primary/5 border border-primary/10 rounded-sm p-6 backdrop-blur-sm text-center space-y-4">
                            <h3 className="font-serif text-xl text-primary">Ready to train</h3>
                            <p className="text-primary/60 font-sans">
                                Click &quot;Start Session&quot; to begin training, or &quot;Generate New&quot; to create fresh puzzles from your games.
                            </p>
                        </div>
                    )}
                    {statusLoadFailed && (
                        <div className="bg-red-500/5 border border-red-500/20 rounded-sm p-6 text-center space-y-4" role="alert" aria-live="assertive">
                            <h3 className="font-serif text-xl text-primary">Couldn&apos;t load your training data</h3>
                            <p className="text-primary/60 font-sans">
                                We couldn&apos;t load your puzzles right now. Please try again.
                            </p>
                            <button
                                type="button"
                                onClick={handleRefreshInsights}
                                disabled={isRefreshingInsights}
                                className={`px-6 py-2 border border-primary/20 text-primary rounded-sm font-serif transition-all km-focus-visible ${isRefreshingInsights ? 'km-interactive-disabled disabled:opacity-50' : 'km-interactive'}`}
                            >
                                {isRefreshingInsights ? 'Retrying...' : 'Retry'}
                            </button>
                        </div>
                    )}
                    {shouldShowErrorCard && (
                        <div className="space-y-4">
                            <JobStatusCard status="failed" error={error ?? 'Failed to generate puzzles'} />
                            <div className="flex flex-wrap justify-center gap-3">
                                <button
                                    type="button"
                                    onClick={handleStartSession}
                                    disabled={!canRetryLoad}
                                    className={`px-6 py-2 border border-primary/20 text-primary rounded-sm font-serif transition-all km-focus-visible ${!canRetryLoad ? 'km-interactive-disabled disabled:opacity-50' : 'km-interactive'}`}
                                >
                                    Retry
                                </button>
                                {userStatus?.has_new_games && (
                                    <button
                                        type="button"
                                        onClick={handleGeneratePuzzles}
                                        disabled={!canRetryLoad}
                                        className={`px-6 py-2 bg-primary text-bg-primary rounded-sm font-serif transition-colors km-focus-visible ${!canRetryLoad ? 'km-interactive-disabled disabled:opacity-50' : 'km-interactive'}`}
                                    >
                                        Generate New
                                    </button>
                                )}
                                {!username && (
                                    <button
                                        type="button"
                                        onClick={() => setEditorOpen(true)}
                                        className="px-6 py-2 bg-primary text-bg-primary rounded-sm font-serif transition-colors km-interactive km-focus-visible"
                                    >
                                        Set Username
                                    </button>
                                )}
                            </div>
                        </div>
                    )}
                    {shouldShowJobStatusCard && job && (
                        <JobStatusCard
                            status={job.status}
                            message={job.message}
                            progress={job.progress}
                            error={job.status === 'failed' ? (job.error || job.message) : undefined}
                            onCancel={handleCancelJob}
                        />
                    )}
                    {shouldShowLoadingCard && (
                        <JobStatusCard
                            status="running"
                            message={isResumingSession ? 'Resuming your session...' : isLoadingStatus ? 'Loading training status...' : 'Loading puzzles...'}
                        />
                    )}
                    {shouldShowPartialDataCard && (
                        <div className="bg-primary/5 border border-primary/10 rounded-sm p-6 backdrop-blur-sm text-center space-y-4">
                            <h3 className="font-serif text-xl text-primary">Some insights are unavailable</h3>
                            <p className="text-primary/60 font-sans">
                                {insightsError || 'We are still syncing your tactical insights. Refresh to try again.'}
                            </p>
                            <button
                                type="button"
                                onClick={handleRefreshInsights}
                                disabled={isRefreshingInsights}
                                className={`px-6 py-2 border border-primary/20 text-primary rounded-sm font-serif transition-all km-focus-visible ${isRefreshingInsights ? 'km-interactive-disabled disabled:opacity-50' : 'km-interactive'}`}
                            >
                                {isRefreshingInsights ? 'Refreshing...' : 'Refresh Insights'}
                            </button>
                        </div>
                    )}
                </div>
            </section>

            {/* Weak Areas Card */}
            {motifPerformance && motifPerformance.weakest_motifs.length > 0 && (
                <section className="bg-primary/5 border border-primary/10 rounded-sm p-6 backdrop-blur-sm">
                    <h3 className="text-lg font-serif text-primary mb-4">
                        Your Weak Areas
                    </h3>
                    <div className="space-y-2">
                        {motifPerformance.motifs
                            .filter(m => m.rank === 'needs_work')
                            .map(motif => (
                                <div key={motif.name} className="flex justify-between items-center p-3 bg-red-500/10 rounded-sm">
                                    <div>
                                        <span className="font-serif text-primary">{motif.name}</span>
                                        <span className="text-xs text-primary/60 ml-2">
                                            {motif.passed}/{motif.total_puzzles} correct
                                        </span>
                                    </div>
                                    <div className="flex items-center gap-2">
                                        <span className="text-red-500 font-mono text-sm">
                                            {Math.round(motif.accuracy * 100)}%
                                        </span>
                                        <span className="text-xs text-primary/40">needs work</span>
                                    </div>
                                </div>
                            ))}
                    </div>
                </section>
            )}

            {/* Warmup Diagnostic Banner */}
            {warmupMode && sessionState === 'active' && (
                <div
                    className="bg-blue-500/10 border border-blue-500/20 rounded-sm p-4 mb-6 text-center animate-teedin"
                    role="status"
                    aria-live="polite"
                >
                    <p className="text-primary font-serif">
                        🎯 Warmup Diagnostic Session
                    </p>
                    <p className="text-primary/60 text-sm font-sans">
                        Complete 5 puzzles to see what stuck while you were away
                    </p>
                </div>
            )}

            {currentPuzzle && ( // Make sure currentPuzzle is defined or access checked
                <section className="grid lg:grid-cols-2 gap-12 lg:gap-24">
                    {/* Chessboard */}
                    <div className="order-2 lg:order-1">
                        <div className="aspect-square w-full max-w-[600px] mx-auto shadow-2xl shadow-primary/5 rounded-sm overflow-hidden border border-primary/10">
                            <Chessboard
                                options={{
                                    position: game.fen(),
                                    onPieceDrop: ({ sourceSquare, targetSquare }) => targetSquare ? onPieceDrop(sourceSquare, targetSquare) : false,
                                    boardOrientation: currentPuzzle.side_to_move === 'white' ? 'white' : 'black',
                                    darkSquareStyle: { backgroundColor: 'var(--color-chess-board-dark)' },
                                    lightSquareStyle: { backgroundColor: 'var(--color-chess-cream-300)' },
                                    squareStyles: clue.squareStyles,
                                }}
                            />
                        </div>
                    </div>

                    {/* Sidebar Controls */}
                    <div className="order-1 lg:order-2 space-y-8 flex flex-col justify-center">
                        <div className="space-y-2">
                            <div className="flex justify-between items-center bg-primary/5 p-4 rounded-sm border-l-2 border-primary">
                                <div className="flex flex-col w-full">
                                    {activeSessionId && sessionSummary && (
                                        <div className="bg-primary/5 border border-primary/20 rounded-lg p-4 mb-4 w-full">
                                            <div className="flex justify-between items-center mb-2">
                                                <span className="font-serif text-primary font-medium">
                                                    Session in Progress
                                                    {sessionSummary.session_type && sessionSummary.session_type !== 'standard'
                                                        ? ` (${sessionSummary.session_type.replace('_', ' ')})`
                                                        : ''}
                                                </span>
                                                <span className="text-sm font-mono text-primary/60">
                                                    {reviewedCount} / {sessionSummary.requested_n}
                                                </span>
                                            </div>

                                            {/* Progress Bar */}
                                            <div className="h-2 bg-primary/10 rounded-full overflow-hidden">
                                                <div
                                                    className="h-full bg-primary transition-all duration-500 ease-out"
                                                    style={{ width: `${Math.min(100, (reviewedCount / sessionSummary.requested_n) * 100)}%` }}
                                                />
                                            </div>

                                            {/* Core Session Stats */}
                                            <div className="flex justify-between mt-3 text-xs">
                                                <div className="flex items-center">
                                                    <span className="text-primary/60 mr-1">🔥</span>
                                                    <span className="text-primary/80">Streak: {streak}</span>
                                                    <span className="text-primary/40 mx-1">|</span>
                                                    <span className="text-primary/60 mr-1">🏆</span>
                                                    <span className="text-primary/80">Best: {bestStreak}</span>
                                                </div>
                                                <div className="flex items-center">
                                                    <span className="text-primary/60 mr-1">💡</span>
                                                    <span className="text-primary/80">Hints: {hintsUsed}</span>
                                                </div>
                                            </div>

                                            <div className="mt-3">
                                                <button
                                                    type="button"
                                                    onClick={() => setShowSessionDetails((prev) => !prev)}
                                                    aria-expanded={showSessionDetails}
                                                    aria-controls="session-details-panel"
                                                    aria-label={sessionDetailsA11yCopy.toggleLabel}
                                                    aria-describedby="session-details-helper"
                                                    className="text-xs font-sans text-primary/70 km-inline-link km-focus-visible"
                                                >
                                                    {showSessionDetails ? 'Hide details' : 'Show details'}
                                                </button>
                                                <span id="session-details-helper" className="sr-only">
                                                    {sessionDetailsA11yCopy.helperText}
                                                </span>
                                                <span className="sr-only" role="status" aria-live="polite" aria-atomic="true">
                                                    {sessionDetailsA11yCopy.liveStatus}
                                                </span>
                                            </div>

                                            {showSessionDetails && (
                                                <div id="session-details-panel" className="mt-3 space-y-3">
                                                    {/* Performance Visualization */}
                                                    {performanceHistory.length > 0 && (
                                                        <div>
                                                            <div className="flex justify-between text-xs text-primary/60 mb-1">
                                                                <span>Recent Performance:</span>
                                                                <span>{calculateRecentPerformance(performanceHistory)}% accuracy (5min)</span>
                                                            </div>
                                                            <div className="flex h-2 rounded-full overflow-hidden bg-primary/10">
                                                                {performanceHistory.slice(-10).map((item, index) => (
                                                                    <div
                                                                        key={index}
                                                                        className={`flex-1 ${item.result === 'pass' ? 'bg-green-500' : 'bg-red-500'}`}
                                                                        title={`${item.result.toUpperCase()} - ${new Date(item.time).toLocaleTimeString()}`}
                                                                    />
                                                                ))}
                                                            </div>
                                                            <div className="flex justify-between text-xs text-primary/60 mt-1">
                                                                <span>
                                                                    Trend:
                                                                    <span className={`ml-1 ${getPerformanceTrend(performanceHistory) === 'improving' ? 'text-green-500' :
                                                                        getPerformanceTrend(performanceHistory) === 'declining' ? 'text-red-500' : 'text-primary/60'
                                                                        }`}>
                                                                        {getPerformanceTrend(performanceHistory) === 'improving' ? '↗ Improving' :
                                                                            getPerformanceTrend(performanceHistory) === 'declining' ? '↘ Declining' : '→ Stable'}
                                                                    </span>
                                                                </span>
                                                                <span>
                                                                    Time: {timer.currentPuzzleTime}s
                                                                </span>
                                                            </div>
                                                        </div>
                                                    )}

                                                    {/* Timed Session Timer */}
                                                    {sessionSummary.session_type === 'timed' && timer.timeRemaining > 0 && (
                                                        <div className="text-center">
                                                            <span className={`font-mono text-sm ${timer.timeRemaining < 60 ? 'text-red-500' : 'text-primary/80'}`}>
                                                                Time Remaining: {Math.floor(timer.timeRemaining / 60)}:{(timer.timeRemaining % 60).toString().padStart(2, '0')}
                                                            </span>
                                                        </div>
                                                    )}
                                                </div>
                                            )}

                                            {isResumingSession && (
                                                <div className="text-xs text-center mt-2 text-primary/60 animate-pulse">
                                                    Resuming previous session...
                                                </div>
                                            )}
                                        </div>
                                    )}

                                    <div className="flex justify-between items-center">
                                        <div className="flex items-center gap-2">
                                            <span className="font-serif text-xl text-primary">
                                                {currentPuzzle.title || "Puzzle"}
                                                <span className="text-base font-normal opacity-50 ml-2 font-sans">
                                                    {currentIndex + 1} / {puzzles.length}
                                                </span>
                                            </span>
                                            {currentPuzzle.primary_motif && (
                                                <span className="text-sm font-sans text-primary/60 px-2 py-1 bg-primary/10 rounded-sm">
                                                    {currentPuzzle.primary_motif}
                                                </span>
                                            )}
                                        </div>
                                    </div>
                                </div>
                                <span className="font-sans text-sm tracking-wide uppercase text-primary/60">
                                    {currentPuzzle.side_to_move === 'white' ? 'White to Move' : 'Black to Move'}
                                </span>
                            </div>
                        </div>

                        {/* Status Area */}
                        <div className="min-h-[100px] flex items-center justify-center text-center p-6 border border-primary/10 rounded-sm relative overflow-hidden">
                            {status === 'solving' && clue.clueStage === 0 && <p className="text-primary/60 font-serif text-lg italic">Find the best move...</p>}
                            {status === 'solving' && clue.clueStage === 1 && (
                                <p className="text-primary/80 font-sans text-sm">
                                    {clue.pieceHint || 'Move the correct piece'}
                                </p>
                            )}
                            {status === 'correct' && (
                                <div className="text-center">
                                    <p className="text-green-600 font-serif text-2xl animate-teedin">Correct! Excellent.</p>
                                    {lastFeedback && (
                                        <p className="text-green-600 font-sans text-sm mt-2 animate-teedin">{lastFeedback}</p>
                                    )}
                                </div>
                            )}
                            {status === 'incorrect' && (
                                <div className="text-center">
                                    <p className="text-red-500 font-serif text-2xl animate-teedin">Incorrect.</p>
                                    {lastFeedback && (
                                        <p className="text-red-500 font-sans text-sm mt-2 animate-teedin">{lastFeedback}</p>
                                    )}
                                </div>
                            )}
                            {status === 'revealed' && (
                                <div>
                                    <p className="text-primary/60 font-sans text-xs uppercase tracking-widest mb-1">Solution</p>
                                    <p className="text-primary font-mono text-xl">{currentPuzzle.best_move_uci}</p>
                                </div>
                            )}
                        </div>

                        {/* Actions */}
                        <div className="space-y-6">
                            {/* Type Move Toggle */}
                            <div className="flex justify-between items-center px-2">
                                <span className="text-xs text-primary/40 uppercase tracking-widest font-sans">Input Method</span>
                                <button
                                    type="button"
                                    onClick={() => setShowUciInput(!showUciInput)}
                                    className="km-interactive km-focus-visible km-inline-link text-primary text-xs font-serif underline decoration-primary/30 underline-offset-4 transition-colors">
                                    {showUciInput ? 'Switch to Drag & Drop' : 'Type Move Manually'}
                                </button>
                            </div>

                            {showUciInput && (
                                <div className="animate-switchedin">
                                    <input
                                        type="text"
                                        placeholder="e.g. e2e4"
                                        value={userMove}
                                        onChange={(e) => setUserMove(e.target.value)}
                                        className="w-full bg-primary/5 border border-primary/20 p-3 rounded-sm text-primary font-mono text-center focus:outline-none focus:border-primary/60 transition-colors"
                                        onKeyPress={(e) => e.key === 'Enter' && handleCheckAnswer()}
                                    />
                                </div>
                            )}

                            {status === 'solving' && (
                                <div className="grid grid-cols-3 gap-4">
                                    <button
                                        type="button"
                                        onClick={handleCheckAnswer}
                                        disabled={!userMove}
                                        aria-label={puzzleActionA11yCopy.checkMoveLabel}
                                        className={`px-6 py-4 bg-primary text-bg-primary rounded-sm font-serif text-lg transition-all shadow-lg shadow-primary/5 km-focus-visible disabled:opacity-50 ${!userMove ? 'km-interactive-disabled' : 'km-interactive'}`}>
                                        Check Move
                                    </button>
                                    <button
                                        type="button"
                                        onClick={activeSessionId ? handleUseHint : handleClue}
                                        disabled={activeSessionId
                                            ? (!currentPuzzle?.best_move_uci || hintsUsed >= 3)
                                            : clue.isDisabled}
                                        aria-label={puzzleActionA11yCopy.hintLabel}
                                        className="px-6 py-4 border border-primary/20 text-primary rounded-sm font-serif text-lg transition-all km-interactive km-focus-visible disabled:opacity-50 disabled:cursor-default">
                                        {activeSessionId ? `Hint (${hintsUsed}/3)` : 'Clue'}
                                    </button>
                                    <button
                                        type="button"
                                        onClick={handleRevealSolution}
                                        aria-label={puzzleActionA11yCopy.revealLabel}
                                        className="px-6 py-4 border border-primary/10 text-primary/70 rounded-sm font-serif text-lg transition-all km-interactive km-focus-visible hover:text-primary hover:border-primary/30">
                                        Reveal
                                    </button>
                                </div>
                            )}
                            {(status === 'correct' || status === 'revealed') && (
                                <div className="space-y-4">
                                    {/* Performance Stats for this puzzle */}
                                    {currentPuzzle?.attempts !== undefined && (
                                        <div className="bg-primary/5 p-3 rounded-sm text-sm">
                                            <div className="flex justify-between">
                                                <span className="text-primary/60">Puzzle Stats:</span>
                                                <span className="font-mono">
                                                    {currentPuzzle.pass_count || 0}/{currentPuzzle.attempts || 0}
                                                    {currentPuzzle.attempts ? ` (${Math.round(((currentPuzzle.pass_count || 0) / currentPuzzle.attempts) * 100)}%)` : ''}
                                                </span>
                                            </div>
                                            {currentPuzzle.next_due_at && (
                                                <div className="flex justify-between mt-1">
                                                    <span className="text-primary/60">Next Review:</span>
                                                    <span className="font-mono">
                                                        {new Date(currentPuzzle.next_due_at).toLocaleDateString()}
                                                    </span>
                                                </div>
                                            )}
                                        </div>
                                    )}

                                    {sessionState === 'completed' ? (
                                        sessionSummary ? (
                                            <p className="text-center text-primary/60 font-sans text-sm py-4">
                                                Session complete — see your summary below.
                                            </p>
                                        ) : (
                                            <Link
                                                to="/dashboard"
                                                className="w-full block text-center px-6 py-4 bg-green-600 text-white rounded-sm font-serif text-lg transition-all km-interactive km-focus-visible shadow-lg shadow-green-900/20">
                                                Back to Dashboard
                                            </Link>
                                        )
                                    ) : (
                                        <button
                                            type="button"
                                            onClick={handleAdvancePuzzle}
                                            disabled={finishButtonDisabled}
                                            className={`w-full px-6 py-4 bg-green-600 text-white rounded-sm font-serif text-lg transition-all shadow-lg shadow-green-900/20 km-focus-visible ${finishButtonDisabled ? 'km-interactive-disabled' : 'km-interactive'} flex items-center justify-center`}>
                                            {sessionState === 'completing' ? (
                                                <>
                                                    <span className="animate-spin h-5 w-5 border-2 border-white/20 border-t-white rounded-full mr-2"></span>
                                                    Recording Session...
                                                </>
                                            ) : isFinalPuzzle ? 'All Done' : 'Next Puzzle →'}
                                        </button>
                                    )}
                                </div>
                            )}
                            {status === 'incorrect' && (
                                <div className="space-y-4">
                                    {/* Detailed feedback for incorrect answers */}
                                    {lastFeedback && (
                                        <div className="bg-red-500/10 border border-red-500/20 p-3 rounded-sm text-sm">
                                            <p className="text-red-500 font-sans">{lastFeedback}</p>
                                        </div>
                                    )}

                                    <div className="grid grid-cols-2 gap-4">
                                        <button
                                            type="button"
                                            onClick={async () => {
                                                await handleReviewPuzzle('fail');
                                                setStatus('solving');
                                                setUserMove('');
                                                setGame(new Chess(currentPuzzle.fen));
                                                clue.reset();
                                            }}
                                            className="px-6 py-4 border border-primary/20 text-primary rounded-sm font-serif text-lg transition-all km-interactive km-focus-visible">
                                            Mark as Failed & Try Again
                                        </button>
                                        <button
                                            type="button"
                                            onClick={handleRevealSolution}
                                            aria-label={puzzleActionA11yCopy.showSolutionLabel}
                                            className="px-6 py-4 bg-primary text-bg-primary rounded-sm font-serif text-lg transition-all km-interactive km-focus-visible">
                                            Show Solution
                                        </button>
                                    </div>

                                    {/* Special button for completing session when final puzzle is failed */}
                                    {isFinalPuzzle && (
                                        <button
                                            type="button"
                                            onClick={async () => {
                                                // Guard re-entry (same as handleAdvancePuzzle) so a fast
                                                // double-click can't fire two concurrent complete calls
                                                // before `disabled` catches up with sessionState.
                                                if (isAdvancingPuzzle.current) return;
                                                isAdvancingPuzzle.current = true;
                                                try {
                                                    await handleReviewPuzzle('fail');
                                                    // Finishing the final puzzle (as a fail) ends the session.
                                                    setReviewedCount(prev => prev + 1);
                                                    await handleCompleteSession();
                                                } finally {
                                                    isAdvancingPuzzle.current = false;
                                                }
                                            }}
                                            disabled={sessionState === 'completing'}
                                            className="w-full px-6 py-4 bg-orange-600 text-white rounded-sm font-serif text-lg transition-all km-focus-visible km-interactive mt-4">
                                            {sessionState === 'completing' ? (
                                                <>
                                                    <span className="animate-spin h-5 w-5 border-2 border-white/20 border-t-white rounded-full mr-2 inline-block"></span>
                                                    Recording Session...
                                                </>
                                            ) : (
                                                'Mark as Failed & Complete Session'
                                            )}
                                        </button>
                                    )}
                                </div>
                            )}
                        </div>
                    </div>
                </section>
            )}

            {/* Session Summary */}
            {sessionSummary && (
                <>
                    {warmupMode ? (
                        <WarmupSummary
                            sessionSummary={sessionSummary}
                            onContinue={() => {
                                setWarmupMode(false);
                                navigate('/dashboard');
                            }}
                        />
                    ) : (
                        <SessionSummaryCard
                            sessionSummary={sessionSummary}
                            achievements={achievements}
                            onStartNewSession={() => {
                                setSessionSummary(null);
                                setLastFeedback('');
                                handleStartSession();
                            }}
                        />
                    )}
                </>
            )}

            {/* Recent Sessions */}
            <RecentSessionsCard sessions={recentSessions} />

            {/* Achievements Progress */}
            <AchievementsList achievements={achievements} />

            {/* Chess Pattern Mastery */}
            {motifPerformance && motifPerformance.motifs.length > 0 && (
                <section className="bg-primary/5 border border-primary/10 rounded-sm p-6 backdrop-blur-sm">
                    <h3 className="text-lg font-serif text-primary mb-4">Chess Pattern Mastery</h3>
                    <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
                        {motifPerformance.motifs.map(motif => (
                            <div
                                key={motif.name}
                                className={`p-4 rounded-sm border ${
                                    motif.rank === 'mastered'
                                        ? 'bg-green-500/10 border-green-500/30'
                                        : motif.rank === 'learning'
                                        ? 'bg-yellow-500/10 border-yellow-500/30'
                                        : 'bg-red-500/10 border-red-500/30'
                                }`}
                            >
                                <h4 className="font-serif text-primary mb-1">{motif.name}</h4>
                                <div className="flex justify-between text-sm">
                                    <span className="text-primary/60">
                                        {motif.passed}/{motif.total_puzzles} solved
                                    </span>
                                    <span className={`font-mono ${
                                        motif.rank === 'mastered' ? 'text-green-600' :
                                        motif.rank === 'learning' ? 'text-yellow-600' :
                                        'text-red-500'
                                    }`}>
                                        {Math.round(motif.accuracy * 100)}%
                                    </span>
                                </div>

                                {/* Progress bar */}
                                <div className="mt-2 h-2 bg-primary/10 rounded-full overflow-hidden">
                                    <div
                                        className={`h-full ${
                                            motif.rank === 'mastered' ? 'bg-green-500' :
                                            motif.rank === 'learning' ? 'bg-yellow-500' :
                                            'bg-red-500'
                                        }`}
                                        style={{ width: `${motif.accuracy * 100}%` }}
                                    />
                                </div>
                            </div>
                        ))}
                    </div>
                </section>
            )}
        </div>
    );
}
