import { useCallback, useEffect, useRef, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { Chessboard } from 'react-chessboard';
import { Chess } from 'chess.js';
import { useChessUsername } from '../context/ChessUsernameContext';
import { getLibraryPuzzle, reviewPuzzle, type LibraryPuzzle as LibraryPuzzleType } from '../api/puzzles';
import { ApiError } from '../api/core';

type SolveStatus = 'solving' | 'correct' | 'incorrect' | 'revealed';

function formatSolveTime(ms: number): string {
    if (ms < 60000) {
        return `${Math.round(ms / 1000)}s`;
    }
    const minutes = Math.floor(ms / 60000);
    const seconds = Math.round((ms % 60000) / 1000);
    return `${minutes}m ${seconds}s`;
}

export default function LibraryPuzzle() {
    const { puzzleId } = useParams<{ puzzleId: string }>();
    const { username } = useChessUsername();

    const [puzzle, setPuzzle] = useState<LibraryPuzzleType | null>(null);
    const [isLoading, setIsLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);

    const [game, setGame] = useState(new Chess());
    const [status, setStatus] = useState<SolveStatus>('solving');
    const [userMove, setUserMove] = useState('');
    const [showUciInput, setShowUciInput] = useState(false);

    // Solve timer
    const solveStartRef = useRef<number>(0);

    // Feedback after recording
    const [recorded, setRecorded] = useState(false);
    const [nextDueAt, setNextDueAt] = useState<string | null>(null);
    const [feedback, setFeedback] = useState('');
    const [isRecording, setIsRecording] = useState(false);
    const [solveTimeMs, setSolveTimeMs] = useState<number | null>(null);

    const fetchPuzzle = useCallback(async () => {
        if (!username || !puzzleId) return;
        setIsLoading(true);
        setError(null);
        try {
            const found = await getLibraryPuzzle(puzzleId, username);
            setPuzzle(found);
            setGame(new Chess(found.fen));
            solveStartRef.current = Date.now();
        } catch (err) {
            if (err instanceof ApiError && err.statusCode === 404) {
                setError('Puzzle not found');
            } else {
                setError(err instanceof Error ? err.message : 'Failed to load puzzle');
            }
        } finally {
            setIsLoading(false);
        }
    }, [username, puzzleId]);

    useEffect(() => {
        fetchPuzzle();
    }, [fetchPuzzle]);

    const handleRecordResult = async (result: 'pass' | 'fail') => {
        if (!puzzle || !username || isRecording) return;
        setIsRecording(true);
        const elapsed = solveStartRef.current > 0 ? Date.now() - solveStartRef.current : undefined;
        if (elapsed) setSolveTimeMs(elapsed);
        try {
            const res = await reviewPuzzle(puzzle.id, username, result, elapsed);
            setRecorded(true);
            setNextDueAt(res.next_due_at);
            setFeedback(res.feedback);
        } catch (err) {
            console.error('Failed to record result:', err);
            setError(err instanceof Error ? `Failed to save result: ${err.message}` : 'Failed to save puzzle result');
        } finally {
            setIsRecording(false);
        }
    };

    const onPieceDrop = (sourceSquare: string, targetSquare: string) => {
        if (!puzzle || status === 'correct' || status === 'revealed') return false;
        try {
            const move = game.move({ from: sourceSquare, to: targetSquare, promotion: 'q' });
            if (move === null) return false;
            setGame(new Chess(game.fen()));
            const uciMove = `${move.from}${move.to}${move.promotion || ''}`;
            setUserMove(uciMove);
            const normalizedBestMove = puzzle.best_move_uci.toLowerCase();
            if (uciMove === normalizedBestMove) {
                setStatus('correct');
                handleRecordResult('pass');
            } else {
                setStatus('incorrect');
            }
            return true;
        } catch {
            return false;
        }
    };

    const handleCheckAnswer = () => {
        if (!puzzle) return;
        const normalizedUserMove = userMove.trim().toLowerCase();
        const normalizedBestMove = puzzle.best_move_uci.toLowerCase();
        if (normalizedUserMove === normalizedBestMove) {
            setStatus('correct');
            handleRecordResult('pass');
        } else {
            setStatus('incorrect');
        }
    };

    const handleRevealSolution = () => {
        if (!puzzle) return;
        setStatus('revealed');
        const bestMove = puzzle.best_move_uci.toLowerCase();
        setUserMove(bestMove);
        const solutionGame = new Chess(puzzle.fen);
        const from = bestMove.slice(0, 2);
        const to = bestMove.slice(2, 4);
        const promotion = bestMove.slice(4, 5);
        solutionGame.move({ from, to, promotion: promotion || undefined });
        setGame(solutionGame);
        handleRecordResult('fail');
    };

    const handleMarkFailedRetry = async () => {
        if (!puzzle) return;
        await handleRecordResult('fail');
        setStatus('solving');
        setUserMove('');
        setGame(new Chess(puzzle.fen));
        setRecorded(false);
        setNextDueAt(null);
        setFeedback('');
        setSolveTimeMs(null);
        solveStartRef.current = Date.now();
    };

    if (isLoading) {
        return (
            <div className="space-y-12 animate-teedin">
                <div className="text-center text-primary/40 py-12">
                    <span className="animate-pulse font-sans">Loading puzzle...</span>
                </div>
            </div>
        );
    }

    if (error || !puzzle) {
        return (
            <div className="space-y-12 animate-teedin">
                <section>
                    <Link to="/library" className="text-primary/40 hover:text-primary mb-4 inline-block font-sans text-sm tracking-widest uppercase transition-colors">
                        ← Back to Library
                    </Link>
                    <div className="bg-red-500/10 border border-red-500/20 rounded-sm p-6 text-center">
                        <p className="text-red-500 font-sans">{error || 'Puzzle not found'}</p>
                    </div>
                </section>
            </div>
        );
    }

    const successRate = puzzle.attempts > 0
        ? Math.round((puzzle.pass_count / puzzle.attempts) * 100)
        : null;

    return (
        <div className="space-y-12 animate-teedin">
            {/* Back link + Header */}
            <section>
                <Link to="/library" className="text-primary/40 hover:text-primary mb-4 inline-block font-sans text-sm tracking-widest uppercase transition-colors">
                    ← Back to Library
                </Link>
                <h1 className="text-3xl md:text-4xl font-serif text-primary">
                    {puzzle.title || 'Puzzle'}
                </h1>
            </section>

            {/* Metadata strip */}
            <section className="flex flex-wrap gap-4 text-sm font-sans text-primary/60">
                <span className="px-3 py-1 bg-primary/5 rounded-sm border border-primary/10">
                    {puzzle.difficulty.charAt(0).toUpperCase() + puzzle.difficulty.slice(1)}
                </span>
                {puzzle.primary_motif && (
                    <span className="px-3 py-1 bg-primary/5 rounded-sm border border-primary/10">
                        {puzzle.primary_motif}
                    </span>
                )}
                {puzzle.attempts > 0 && (
                    <span className="px-3 py-1 bg-primary/5 rounded-sm border border-primary/10">
                        {puzzle.pass_count}/{puzzle.attempts} solved{successRate !== null ? ` (${successRate}%)` : ''}
                    </span>
                )}
                {puzzle.fail_count > 0 && (
                    <span className="px-3 py-1 bg-red-500/10 rounded-sm border border-red-500/20 text-red-500/80">
                        {puzzle.fail_count} failed
                    </span>
                )}
                {puzzle.last_reviewed_at && (
                    <span className="px-3 py-1 bg-primary/5 rounded-sm border border-primary/10">
                        Last: {new Date(puzzle.last_reviewed_at).toLocaleDateString()}
                    </span>
                )}
                {puzzle.next_due_at && !recorded && (
                    <span className="px-3 py-1 bg-primary/5 rounded-sm border border-primary/10">
                        Due: {new Date(puzzle.next_due_at).toLocaleDateString()}
                    </span>
                )}
            </section>

            {/* Board + Controls */}
            <section className="grid lg:grid-cols-2 gap-12 lg:gap-24">
                {/* Chessboard */}
                <div className="order-2 lg:order-1">
                    <div className="aspect-square w-full max-w-[600px] mx-auto shadow-2xl shadow-primary/5 rounded-sm overflow-hidden border border-primary/10">
                        <Chessboard
                            options={{
                                position: game.fen(),
                                onPieceDrop: ({ sourceSquare, targetSquare }) =>
                                    targetSquare ? onPieceDrop(sourceSquare, targetSquare) : false,
                                boardOrientation: puzzle.side_to_move === 'white' ? 'white' : 'black',
                                darkSquareStyle: { backgroundColor: 'var(--color-chess-brown-700)' },
                                lightSquareStyle: { backgroundColor: 'var(--color-chess-cream-300)' },
                            }}
                        />
                    </div>
                </div>

                {/* Sidebar Controls */}
                <div className="order-1 lg:order-2 space-y-8 flex flex-col justify-center">
                    {/* Side to move */}
                    <div className="bg-primary/5 p-4 rounded-sm border-l-2 border-primary">
                        <span className="font-sans text-sm tracking-wide uppercase text-primary/60">
                            {puzzle.side_to_move === 'white' ? 'White to Move' : 'Black to Move'}
                        </span>
                    </div>

                    {/* Status feedback */}
                    <div className="min-h-[80px] flex items-center justify-center text-center p-6 border border-primary/10 rounded-sm">
                        {status === 'solving' && (
                            <p className="text-primary/60 font-serif text-lg italic">Find the best move...</p>
                        )}
                        {status === 'correct' && (
                            <div className="text-center">
                                <p className="text-green-600 font-serif text-2xl animate-teedin">Correct!</p>
                                {feedback && <p className="text-green-600 font-sans text-sm mt-2">{feedback}</p>}
                            </div>
                        )}
                        {status === 'incorrect' && (
                            <p className="text-red-500 font-serif text-2xl animate-teedin">Incorrect.</p>
                        )}
                        {status === 'revealed' && (
                            <div>
                                <p className="text-primary/60 font-sans text-xs uppercase tracking-widest mb-1">Solution</p>
                                <p className="text-primary font-mono text-xl">{puzzle.best_move_uci}</p>
                            </div>
                        )}
                    </div>

                    {/* Recorded confirmation */}
                    {recorded && (
                        <div className="bg-green-500/10 border border-green-500/20 rounded-sm p-4 text-center animate-teedin">
                            <p className="text-green-600 font-serif font-medium">Recorded</p>
                            <div className="flex items-center justify-center gap-4 mt-2 text-sm font-sans text-green-600/70">
                                {solveTimeMs && (
                                    <span>{formatSolveTime(solveTimeMs)}</span>
                                )}
                                {puzzle.attempts > 0 && (
                                    <span>
                                        Solved {puzzle.pass_count}/{puzzle.attempts} times
                                        ({Math.round((puzzle.pass_count / puzzle.attempts) * 100)}%)
                                    </span>
                                )}
                            </div>
                            {nextDueAt && (
                                <p className="text-green-600/70 font-sans text-sm mt-1">
                                    Next review: {new Date(nextDueAt).toLocaleDateString(undefined, {
                                        weekday: 'long', month: 'short', day: 'numeric'
                                    })}
                                </p>
                            )}
                        </div>
                    )}

                    {/* Actions */}
                    <div className="space-y-4">
                        {/* Manual UCI input toggle */}
                        <div className="flex justify-between items-center px-2">
                            <span className="text-xs text-primary/40 uppercase tracking-widest font-sans">Input Method</span>
                            <button
                                type="button"
                                onClick={() => setShowUciInput(!showUciInput)}
                                className="km-interactive km-focus-visible km-inline-link text-primary text-xs font-serif underline decoration-primary/30 underline-offset-4 transition-colors"
                            >
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
                                    onKeyDown={(e) => e.key === 'Enter' && handleCheckAnswer()}
                                />
                            </div>
                        )}

                        {status === 'solving' && (
                            <div className="grid grid-cols-2 gap-4">
                                <button
                                    type="button"
                                    onClick={handleCheckAnswer}
                                    disabled={!userMove}
                                    className={`px-6 py-4 bg-primary text-bg-primary rounded-sm font-serif text-lg transition-all shadow-lg shadow-primary/5 km-focus-visible disabled:opacity-50 ${!userMove ? 'km-interactive-disabled' : 'km-interactive'}`}
                                >
                                    Check Move
                                </button>
                                <button
                                    type="button"
                                    onClick={handleRevealSolution}
                                    className="px-6 py-4 border border-primary/20 text-primary rounded-sm font-serif text-lg transition-all km-interactive km-focus-visible"
                                >
                                    Reveal
                                </button>
                            </div>
                        )}

                        {status === 'incorrect' && (
                            <div className="grid grid-cols-2 gap-4">
                                <button
                                    type="button"
                                    onClick={handleMarkFailedRetry}
                                    className="px-6 py-4 border border-primary/20 text-primary rounded-sm font-serif text-lg transition-all km-interactive km-focus-visible"
                                >
                                    Try Again
                                </button>
                                <button
                                    type="button"
                                    onClick={handleRevealSolution}
                                    className="px-6 py-4 bg-primary text-bg-primary rounded-sm font-serif text-lg transition-all km-interactive km-focus-visible"
                                >
                                    Show Solution
                                </button>
                            </div>
                        )}

                        {(status === 'correct' || status === 'revealed') && (
                            <Link
                                to="/library"
                                className="block w-full px-6 py-4 bg-green-600 text-white rounded-sm font-serif text-lg text-center transition-all km-interactive km-focus-visible"
                            >
                                Back to Library
                            </Link>
                        )}
                    </div>
                </div>
            </section>
        </div>
    );
}
