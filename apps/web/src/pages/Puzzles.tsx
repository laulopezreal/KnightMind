import { useState, useEffect, useMemo } from 'react';
import { Link } from 'react-router-dom';
import { Chessboard } from 'react-chessboard';
import { Chess } from 'chess.js';
import { generatePuzzles, getDailyPuzzles, getDuePuzzles, cancelJob, ApiError, type Puzzle, startSession, completeSession, getRecentSessions, reviewPuzzle, getSession, type SessionSummary } from '../api';
import { JobStatusCard } from '../components/JobStatusCard';
import { useJobPolling } from '../hooks/useJobPolling';
import { useChessUsername } from '../context/ChessUsernameContext';
import { parseBestMoveUci, getPieceNameAtSquare } from '../utils/puzzle-clue';

type PuzzleStatus = 'solving' | 'correct' | 'incorrect' | 'revealed';
type ClueStage = 0 | 1 | 2;
type SessionState = 'idle' | 'loading' | 'active' | 'completing' | 'completed' | 'error';

export default function Puzzles() {
    const { username, setEditorOpen } = useChessUsername();
    const [puzzles, setPuzzles] = useState<Puzzle[]>([]);
    const [currentIndex, setCurrentIndex] = useState(0);
    const [userMove, setUserMove] = useState('');
    const [status, setStatus] = useState<PuzzleStatus>('solving');
    const [isLoading, setIsLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [showUciInput, setShowUciInput] = useState(false);
    const [activeJobId, setActiveJobId] = useState<string | null>(null);

    // Session state
    const [activeSessionId, setActiveSessionId] = useState<string | null>(null);
    const [sessionSummary, setSessionSummary] = useState<SessionSummary | null>(null);
    const [recentSessions, setRecentSessions] = useState<SessionSummary[]>([]);
    const [reviewedCount, setReviewedCount] = useState(0);
    const [isResumingSession, setIsResumingSession] = useState(false);
    const [sessionState, setSessionState] = useState<SessionState>('idle');
    const [clueStage, setClueStage] = useState<ClueStage>(0);

    // Mock progress for now until we hook up real polling
    // const mockProgress = 0; 


    const currentPuzzle = puzzles[currentIndex];
    const puzzlesAvailable = puzzles.length > 0;
    const isFinalPuzzle = puzzlesAvailable && currentIndex >= puzzles.length - 1;
    const finishButtonDisabled = isFinalPuzzle ? sessionState !== 'active' : false;
    const controlsEnabled = sessionState === 'idle' || sessionState === 'error';

    // Load persisted job and session from local storage on mount or username change
    useEffect(() => {
        if (!username) return;
        const savedJobId = localStorage.getItem(`knightmind:lastJob:${username}`);
        if (savedJobId) {
            setActiveJobId(savedJobId);
        } else {
            setActiveJobId(null);
        }

        const savedSessionId = localStorage.getItem(`knightmind:session:${username}`);

        const loadSessionAndPuzzles = async () => {
            if (!savedSessionId) return;

            try {
                setIsResumingSession(true);
                const session = await getSession(savedSessionId);

                if (session.completed_at) {
                    // Session already completed, clear it
                    localStorage.removeItem(`knightmind:session:${username}`);
                    setActiveSessionId(null);
                    return;
                }

                // Session valid and active
                setActiveSessionId(session.session_id);
                setSessionSummary(session);
                setReviewedCount(session.pass_count + session.fail_count);

                setSessionState('loading');
                setError(null);
                setIsLoading(true);
                try {
                    const response = await getDuePuzzles(username, session.requested_n);
                    setPuzzles(response.puzzles);
                    setCurrentIndex(0);
                    setStatus('solving');
                    if (response.puzzles.length > 0) {
                        setSessionState('active');
                        setError(null);
                    } else {
                        setSessionState('error');
                    }
                } catch (err) {
                    const message = err instanceof Error ? err.message : 'Failed to load puzzles';
                    setError(message);
                    setSessionState(puzzles.length > 0 ? 'active' : 'error');
                } finally {
                    setIsLoading(false);
                }
            } catch (err) {
                // Session not found or error, clear it
                console.error("Failed to resume session:", err);
                localStorage.removeItem(`knightmind:session:${username}`);
                setActiveSessionId(null);
                setSessionState('idle');
            } finally {
                setIsResumingSession(false);
            }
        };

        const loadRecent = async () => {
            try {
                const sessions = await getRecentSessions(username, 5);
                setRecentSessions(sessions);
            } catch (err) {
                setError(err instanceof Error ? err.message : 'Failed to load recent sessions');
            }
        };

        loadSessionAndPuzzles();
        loadRecent();
        // eslint-disable-next-line react-hooks/exhaustive-deps -- run only on username change
    }, [username]);

    const { job, isPolling: isJobPolling } = useJobPolling(activeJobId, {
        enabled: !!activeJobId,
        onSuccess: async () => {
            // Clear local storage on success so we don't start polling old finished jobs next time?
            // Or keep it to show "Success" state persistently until user generates new?
            // Prompt says: "If succeeded/failed, show final state and clear stored job_id (optional)"
            // Let's keep it to show the success card, but maybe trigger auto-refresh.

            // Auto-refresh puzzles
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

            // Clear job ID after a delay or let user clear it?
            // If we clear it immediately, the card disappears. We probably want the card to stay "Success".
            // We can clear localStorage but keep activeJobId in state for this session.
            localStorage.removeItem(`knightmind:lastJob:${username}`);
        },
        onError: (err) => {
            // Similarly clear storage on hard failure so we don't get stuck
            localStorage.removeItem(`knightmind:lastJob:${username}`);
            const message = err instanceof Error ? err.message : 'Failed to generate puzzles';
            if (puzzles.length > 0) {
                setSessionState('active');
                setError(null);
            } else {
                setSessionState('error');
                setError(message);
            }
        }
    });

    const isGenerating = isJobPolling || (job?.status === 'queued' || job?.status === 'running');
    const controlsDisabled = !controlsEnabled || isLoading || isGenerating;

    // Sync job status to local isGenerating for backwards compat with other UI if needed, 
    // but better to rely on 'job' object.

    // ... (keep logic same as original, just updating UI)
    // ... (keep logic same as original, just updating UI)
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
                    setError('No games found. Please import games first.');
                } else {
                    setError(err.detail || err.message);
                }
            } else {
                setError(err instanceof Error ? err.message : 'Failed to generate puzzles');
            }
            if (puzzles.length > 0) {
                setSessionState('active');
                setError(null);
            } else {
                setSessionState('error');
            }
        }
    };

    const handleLoadPuzzles = async () => {
        if (!username.trim()) {
            setError('Please enter a username');
            return;
        }
        // Check if we already have a running job? Maybe not needed.
        setSessionState('loading');
        setIsLoading(true);
        setError(null);

        try {
            const dailyPuzzles = await getDailyPuzzles(username.trim(), 5);
            setPuzzles(dailyPuzzles.puzzles);
            setCurrentIndex(0);
            setStatus('solving');
            setUserMove('');
            setError(null);
            setReviewedCount(0); // Reset reviewed count
            if (dailyPuzzles.puzzles.length > 0) {
                setSessionState('active');
            } else {
                setSessionState('error');
            }
        } catch (err) {
            if (err instanceof ApiError) {
                if (err.statusCode === 404) {
                    setError('No puzzles found. Generate puzzles first.');
                } else {
                    setError(err.detail || err.message);
                }
            } else {
                setError(err instanceof Error ? err.message : 'Failed to load puzzles');
            }
            if (puzzles.length > 0) {
                setSessionState('active');
                setError(null);
            } else {
                setSessionState('error');
            }
        } finally {
            setIsLoading(false);
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

    // Session handlers
    const handleStartSession = async () => {
        if (!username.trim()) {
            setError('Please enter a username');
            return;
        }

        setSessionState('loading');
        setError(null);

        try {
            const { session_id } = await startSession(username.trim(), 5);
            setActiveSessionId(session_id);
            localStorage.setItem(`knightmind:session:${username.trim()}`, session_id);
            setSessionSummary(null);
            setReviewedCount(0);

            // Load puzzles
            // FIX: Use getDuePuzzles for session training
            setIsLoading(true);
            try {
                const response = await getDuePuzzles(username.trim(), 5);
                setPuzzles(response.puzzles);
                setCurrentIndex(0);
                setStatus('solving');
                if (response.puzzles.length > 0) {
                    setSessionState('active');
                    setError(null);
                } else {
                    setSessionState('error');
                }
            } catch (puzErr) {
                const message = puzErr instanceof Error ? puzErr.message : 'Failed to load session puzzles';
                if (puzzles.length > 0) {
                    setSessionState('active');
                    setError(null);
                } else {
                    setSessionState('error');
                    setError(message);
                }
            } finally {
                setIsLoading(false);
            }
        } catch (err) {
            const message = err instanceof Error ? err.message : 'Failed to start session';
            if (puzzles.length > 0) {
                setSessionState('active');
                setError(null);
            } else {
                setSessionState('error');
                setError(message);
            }
        }
    };

    const handleCompleteSession = async () => {
        if (!activeSessionId || !username.trim()) return;

        setSessionState('completing');

        try {
            const summary = await completeSession(activeSessionId, username.trim());
            setSessionSummary(summary);
            setActiveSessionId(null);
            localStorage.removeItem(`knightmind:session:${username.trim()}`);
            setSessionState('completed');

            // Refresh recent sessions
            const recent = await getRecentSessions(username.trim(), 5);
            setRecentSessions(recent);
        } catch (err) {
            console.error('Failed to complete session:', err);
            setError(err instanceof Error ? err.message : 'Failed to complete session');
            setSessionState('active');
        }
    };

    const handleReviewPuzzle = async (result: 'pass' | 'fail', timeMs?: number) => {
        if (!currentPuzzle || !username.trim()) return;

        try {
            await reviewPuzzle(
                currentPuzzle.id,
                username.trim(),
                result,
                timeMs,
                activeSessionId || undefined
            );

            // Increment reviewed count
            const newCount = reviewedCount + 1;
            setReviewedCount(newCount);

            // Check if session is complete
            if (activeSessionId && newCount >= puzzles.length) {
                await handleCompleteSession();
            }
        } catch (err) {
            console.error('Failed to review puzzle:', err);
            setError(err instanceof Error ? err.message : 'Failed to review puzzle');
        }
    };

    const shouldShowJobStatusCard =
        !!job &&
        (job.status === 'queued' ||
            job.status === 'running' ||
            (!puzzlesAvailable && (job.status === 'succeeded' || job.status === 'failed')));
    const shouldShowErrorCard = sessionState === 'error' && !!error;


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
        if (!currentPuzzle?.best_move_uci) return;
        if (clueStage === 0) {
            setClueStage(1);
        } else if (clueStage === 1) {
            setClueStage(2);
            handleRevealSolution();
        }
    };

    const [game, setGame] = useState(new Chess());

    const bestMoveParsed = useMemo(() => {
        if (!currentPuzzle?.best_move_uci) return { from: '', to: '' };
        return parseBestMoveUci(currentPuzzle.best_move_uci);
    }, [currentPuzzle?.best_move_uci]);

    const clueSquareStyles: Record<string, { backgroundColor: string }> =
        currentPuzzle?.best_move_uci && clueStage >= 1
            ? (() => {
                const { from, to } = bestMoveParsed;
                const highlight = { backgroundColor: 'rgba(255, 235, 59, 0.45)' };
                if (clueStage === 2 && to) return { [from]: highlight, [to]: highlight };
                return from ? { [from]: highlight } : {};
            })()
            : {};

    useEffect(() => {
        if (currentPuzzle) {
            setGame(new Chess(currentPuzzle.fen));
            setClueStage(0);
        }
    }, [currentPuzzle]);

    const onPieceDrop = (sourceSquare: string, targetSquare: string) => {
        if (!currentPuzzle || status === 'correct' || status === 'revealed') return false;
        try {
            const move = game.move({ from: sourceSquare, to: targetSquare, promotion: 'q' });
            if (move === null) return false;
            setClueStage(0);
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
            setClueStage(0);
        }
    };

    const handleAdvancePuzzle = async () => {
        if (status === 'correct') {
            await handleReviewPuzzle('pass');
        }

        if (!isFinalPuzzle) {
            handleNextPuzzle();
        }
    };


    // Helper function to calculate accuracy percentage
    const calculateAccuracy = (passCount: number, failCount: number): number => {
        const total = passCount + failCount;
        return total > 0 ? Math.round((passCount / total) * 100) : 0;
    };

    return (
        <div className="space-y-12 animate-teedin">
            <section>
                <Link to="/" className="text-primary/40 hover:text-primary mb-4 inline-block font-sans text-sm tracking-widest uppercase transition-colors">
                    ← Return Home
                </Link>
                <div className="flex justify-between items-end">
                    <div>
                        <h1 className="text-4xl md:text-5xl font-serif text-primary mb-2">Daily Puzzles</h1>
                        <p className="text-lg text-primary/60 font-sans">Tactical patterns from your own games.</p>
                    </div>
                </div>
            </section>

            {/* Controls */}
            <section className="bg-primary/5 border border-primary/10 rounded-sm p-6 backdrop-blur-sm">
                <div className="flex flex-col md:flex-row gap-6 relative items-end">
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
                    <div className="flex gap-4">
                        {!activeSessionId && (
                            <button type="button" onClick={handleStartSession} disabled={controlsDisabled}
                                className={`px-6 py-2 bg-accent text-bg-primary rounded-sm font-serif transition-colors km-focus-visible ${controlsDisabled ? 'km-interactive-disabled disabled:opacity-50' : 'km-interactive'}`}>
                                Start Session
                            </button>
                        )}
                        <button type="button" onClick={handleLoadPuzzles} disabled={controlsDisabled}
                            className={`px-6 py-2 border border-primary/20 text-primary rounded-sm font-serif transition-all km-focus-visible ${controlsDisabled ? 'km-interactive-disabled disabled:opacity-50' : 'km-interactive'}`}>
                            {isLoading ? 'Loading...' : 'Load Puzzles'}
                        </button>
                        <button type="button" onClick={handleGeneratePuzzles} disabled={controlsDisabled}
                            className={`px-6 py-2 bg-primary text-bg-primary rounded-sm font-serif transition-colors km-focus-visible ${controlsDisabled ? 'km-interactive-disabled disabled:opacity-50' : 'km-interactive'}`}>
                            {isGenerating ? 'Generating...' : 'Generate New'}
                        </button>
                    </div>
                </div>

                {/* Job Status / Error Area */}
                <div className="mt-6">
                    {shouldShowErrorCard && (
                        <JobStatusCard status="failed" error={error ?? 'Failed to generate puzzles'} />
                    )}
                    {shouldShowJobStatusCard && job && (
                        <JobStatusCard
                            status={job.status}
                            message={job.message}
                            progress={job.progress}
                            error={job.status === 'failed' ? job.message : undefined}
                            onCancel={handleCancelJob}
                        />
                    )}
                    {isLoading && !isGenerating && (
                        <JobStatusCard status="running" message="Loading puzzles..." />
                    )}
                </div>
            </section>

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
                                    darkSquareStyle: { backgroundColor: 'var(--color-chess-brown-700)' },
                                    lightSquareStyle: { backgroundColor: 'var(--color-chess-cream-300)' },
                                    squareStyles: clueSquareStyles,
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
                                                <span className="font-serif text-primary font-medium">Session in Progress</span>
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

                                            {isResumingSession && (
                                                <div className="text-xs text-center mt-2 text-primary/60 animate-pulse">
                                                    Resuming previous session...
                                                </div>
                                            )}
                                        </div>
                                    )}

                                    <div className="flex justify-between items-center">
                                        <span className="font-serif text-xl text-primary">
                                            {currentPuzzle.title || "Puzzle"}
                                            <span className="text-base font-normal opacity-50 ml-2 font-sans">
                                                {currentIndex + 1} / {puzzles.length}
                                            </span>
                                        </span>
                                    </div>
                                </div>
                                <span className="font-sans text-sm tracking-wide uppercase text-primary/60">
                                    {currentPuzzle.side_to_move === 'white' ? 'White to Move' : 'Black to Move'}
                                </span>
                            </div>
                        </div>

                        {/* Status Area */}
                        <div className="min-h-[100px] flex items-center justify-center text-center p-6 border border-primary/10 rounded-sm relative overflow-hidden">
                            {status === 'solving' && clueStage === 0 && <p className="text-primary/60 font-serif text-lg italic">Find the best move...</p>}
                            {status === 'solving' && clueStage === 1 && (
                                <p className="text-primary/80 font-sans text-sm">
                                    {currentPuzzle?.best_move_uci
                                        ? getPieceNameAtSquare(currentPuzzle.fen, bestMoveParsed.from)
                                        : 'Move the correct piece'}
                                </p>
                            )}
                            {status === 'correct' && <p className="text-green-600 font-serif text-2xl animate-teedin">Correct! Excellent.</p>}
                            {status === 'incorrect' && <p className="text-red-500 font-serif text-2xl animate-teedin">Incorrect.</p>}
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
                                        className={`px-6 py-4 bg-primary text-bg-primary rounded-sm font-serif text-lg transition-all shadow-lg shadow-primary/5 km-focus-visible disabled:opacity-50 ${!userMove ? 'km-interactive-disabled' : 'km-interactive'}`}>
                                        Check Move
                                    </button>
                                    <button
                                        type="button"
                                        onClick={handleClue}
                                        disabled={!currentPuzzle?.best_move_uci || clueStage === 2}
                                        className="px-6 py-4 border border-primary/20 text-primary rounded-sm font-serif text-lg transition-all km-interactive km-focus-visible disabled:opacity-50 disabled:cursor-default">
                                        Clue
                                    </button>
                                    <button
                                        type="button"
                                        onClick={handleRevealSolution}
                                        className="px-6 py-4 border border-primary/20 text-primary rounded-sm font-serif text-lg transition-all km-interactive km-focus-visible">
                                        Reveal
                                    </button>
                                </div>
                            )}
                            {(status === 'correct' || status === 'revealed') && (
                                <button
                                    type="button"
                                    onClick={handleAdvancePuzzle}
                                    disabled={finishButtonDisabled}
                                    className={`w-full px-6 py-4 bg-green-600 text-white rounded-sm font-serif text-lg transition-all shadow-lg shadow-green-900/20 km-focus-visible ${finishButtonDisabled ? 'km-interactive-disabled' : 'km-interactive'}`}>
                                    {isFinalPuzzle ? 'All Done' : 'Next Puzzle →'}
                                </button>
                            )}
                            {status === 'incorrect' && (
                                <div className="space-y-4">
                                    <button
                                        type="button"
                                        onClick={async () => {
                                            await handleReviewPuzzle('fail');
                                            setStatus('solving');
                                            setUserMove('');
                                            setGame(new Chess(currentPuzzle.fen));
                                            setClueStage(0);
                                        }}
                                        className="w-full px-6 py-4 border border-primary/20 text-primary rounded-sm font-serif text-lg transition-all km-interactive km-focus-visible">
                                        Mark as Failed & Try Again
                                    </button>
                                </div>
                            )}
                        </div>
                    </div>
                </section>
            )}

            {/* Session Summary */}
            {sessionSummary && (
                <section className="bg-primary/5 border border-primary/10 rounded-sm p-8 backdrop-blur-sm animate-teedin">
                    <h2 className="text-2xl font-serif text-primary mb-6">Session Complete!</h2>
                    <div className="grid grid-cols-2 md:grid-cols-4 gap-6 mb-6">
                        <div className="text-center">
                            <div className="text-3xl font-serif text-green-600">{sessionSummary.pass_count}</div>
                            <div className="text-xs uppercase tracking-widest text-primary/40 mt-1">Passed</div>
                        </div>
                        <div className="text-center">
                            <div className="text-3xl font-serif text-red-500">{sessionSummary.fail_count}</div>
                            <div className="text-xs uppercase tracking-widest text-primary/40 mt-1">Failed</div>
                        </div>
                        <div className="text-center">
                            <div className="text-3xl font-serif text-primary">
                                {calculateAccuracy(sessionSummary.pass_count, sessionSummary.fail_count)}%
                            </div>
                            <div className="text-xs uppercase tracking-widest text-primary/40 mt-1">Accuracy</div>
                        </div>
                        <div className="text-center">
                            <div className="text-3xl font-serif text-primary">
                                {Math.floor(sessionSummary.total_time_ms / 60000)}m {Math.floor((sessionSummary.total_time_ms % 60000) / 1000)}s
                            </div>
                            <div className="text-xs uppercase tracking-widest text-primary/40 mt-1">Total Time</div>
                        </div>
                    </div>
                    <button
                        type="button"
                        onClick={() => {
                            setSessionSummary(null);
                            handleStartSession();
                        }}
                        className="w-full px-6 py-3 bg-primary text-bg-primary rounded-sm font-serif transition-colors km-interactive km-focus-visible">
                        Start New Session
                    </button>
                </section>
            )}

            {/* Recent Sessions */}
            {recentSessions.length > 0 && (
                <section className="bg-primary/5 border border-primary/10 rounded-sm p-6 backdrop-blur-sm">
                    <h3 className="text-lg font-serif text-primary mb-4">Recent Sessions</h3>
                    <div className="space-y-2">
                        {recentSessions.map((session) => (
                            <div key={session.session_id} className="flex justify-between items-center p-3 bg-primary/5 rounded-sm text-sm">
                                <div className="flex gap-4">
                                    <span className="text-green-600">{session.pass_count}P</span>
                                    <span className="text-red-500">{session.fail_count}F</span>
                                    <span className="text-primary/60">
                                        {calculateAccuracy(session.pass_count, session.fail_count)}%
                                    </span>
                                </div>
                                <span className="text-primary/40 text-xs">
                                    {new Date(session.created_at).toLocaleDateString()}
                                </span>
                            </div>
                        ))}
                    </div>
                </section>
            )}
        </div>
    );
}
