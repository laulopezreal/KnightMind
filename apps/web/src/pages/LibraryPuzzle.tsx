import { LOCALE } from '../utils/locale';
import { useCallback, useEffect, useRef, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { AccessibleChessboard } from '../components/AccessibleChessboard';
import { Chess } from 'chess.js';
import { useChessUsername } from '../context/ChessUsernameContext';
import { getLibraryPuzzle, getPuzzleDiagnosis, getSimilarPuzzles, reviewPuzzle, type LibraryPuzzle as LibraryPuzzleType, type PuzzleDiagnosis, type SimilarPuzzlesResponse } from '../api/puzzles';
import { MistakeDiagnosisCard } from '../components/MistakeDiagnosisCard';
import { SimilarWeaknessCard } from '../components/SimilarWeaknessCard';
import { ApiError } from '../api/core';
import { DataStateError, DataStateOffline } from '../components/DataState';
import { useOnlineStatus } from '../hooks/useOnlineStatus';
import { useAsyncData } from '../hooks/useAsyncData';
import { ConnectAccountEmpty } from '../components/ConnectAccountEmpty';

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
    const [recordError, setRecordError] = useState<string | null>(null);

    // Post-mortem diagnosis. Fetched only once the puzzle is resolved, because
    // the evidence names the solution move.
    const [diagnosis, setDiagnosis] = useState<PuzzleDiagnosis | null>(null);
    const [diagnosisLoading, setDiagnosisLoading] = useState(false);
    // Siblings are post-mortem content for the same reason the diagnosis is:
    // naming the shared motif before the attempt would hand over the tactic.
    const [similar, setSimilar] = useState<SimilarPuzzlesResponse | null>(null);

    const online = useOnlineStatus();

    // Everything above that describes *this* puzzle rather than the page.
    //
    // Kept adjacent to the declarations on purpose: this route can navigate to
    // itself. SimilarWeaknessCard links from /library/:id to /library/:id, so
    // React reuses the component instead of remounting it, and any state left
    // behind is inherited by the next puzzle. When `status` stayed 'revealed',
    // the sibling opened with its answer already granted and its diagnosis —
    // which names the best move — was fetched with reveal=true, with no attempt
    // made. Add state above, reset it here.
    const resetPuzzleState = useCallback(() => {
        setStatus('solving');
        setUserMove('');
        setShowUciInput(false);
        setRecorded(false);
        setNextDueAt(null);
        setFeedback('');
        setSolveTimeMs(null);
        setRecordError(null);
        setDiagnosis(null);
        setDiagnosisLoading(false);
        setSimilar(null);
    }, []);

    // The staleness guard, the loading flag and the error slot all live in
    // useAsyncData now. What stays here is the part specific to this page: a 404
    // has to read "Puzzle not found" rather than the transport's message,
    // because the error branch below distinguishes the two to decide whether to
    // offer a Retry.
    const {
        data: loaded,
        error,
        busy: isLoading,
        reload: fetchPuzzle,
    } = useAsyncData<{ puzzle: LibraryPuzzleType }>(
        async () => {
            try {
                const found = await getLibraryPuzzle(puzzleId!, username!);
                // Construct here, inside the try, exactly where the old code did.
                // chess.js throws on a malformed FEN, and that throw belongs in
                // this catch -- moving it into render turned a bad FEN from an
                // error card with a Retry into an unmounted page.
                new Chess(found.fen);
                // A fresh wrapper per load: the reset below keys on its identity,
                // so a reload of the SAME id still resets. Keying on puzzle.id
                // did not, which left a revealed solution and a running solve
                // clock attached to the next load of that id.
                return { puzzle: found };
            } catch (err) {
                if (err instanceof ApiError && err.statusCode === 404) {
                    throw new Error('Puzzle not found');
                }
                throw err;
            }
        },
        [username, puzzleId],
        { enabled: Boolean(username && puzzleId), errorMessage: 'Failed to load puzzle' },
    );
    const puzzle = loaded?.puzzle ?? null;

    // Success side effects, previously inline in the fetch. Keyed on the loaded
    // puzzle's identity, which changes on every successful load -- including a
    // reload of the same id -- so the board and timer reset exactly when they
    // did before.
    // `readyFor` is the puzzle id that the per-puzzle state below describes.
    //
    // The reset happens DURING RENDER, not in an effect, and that is the whole
    // point. The old code called setPuzzle and resetPuzzleState in one
    // synchronous block, so `puzzle` was never the new puzzle while `status`
    // still described the old one. Moving the fetch into useAsyncData splits
    // those: the hook commits the data, and an effect would reset a render
    // later. That extra render is not cosmetic -- `resolved` below is true
    // inside it, which fires the sibling's diagnosis (whose evidence names the
    // best move) with no attempt made, and it leaves the previous puzzle's
    // resolved controls on screen for a frame.
    //
    // Adjusting state during render is React's documented answer to "reset
    // state when the data changes": the update is applied before the browser
    // sees anything, so the new puzzle and its fresh state land together. The
    // id comparison is what makes it converge -- the second pass sees
    // `puzzle.id === readyFor` and skips.
    // Keyed on the LOAD, not the puzzle id. Every successful fetch returns a new
    // wrapper, so this fires for a reload of the same id and for a username
    // change that refetches it -- both of which an id-keyed guard silently
    // skipped, carrying a revealed solution and a stale solve clock into the
    // next load.
    const [readyFor, setReadyFor] = useState<{ puzzle: LibraryPuzzleType } | null>(null);
    if (loaded && loaded !== readyFor) {
        setReadyFor(loaded);
        // Safe in render: the fetcher already parsed this FEN, so it cannot throw.
        setGame(new Chess(loaded.puzzle.fen));
        resetPuzzleState();
    }

    // The ref write stays in an effect: render must remain pure, and the solve
    // clock only needs to start once the puzzle is actually on screen.
    useEffect(() => {
        if (readyFor) solveStartRef.current = Date.now();
    }, [readyFor]);

    // The diagnosis is post-mortem content: its evidence names the solution, so
    // it is not even requested until the puzzle has been solved or revealed.
    // `reveal` is passed for the same reason getLibraryPuzzle passes it — by
    // this point the answer is already on screen.
    // Gated on the loaded puzzle matching the route, not just on `status`.
    //
    // resetPuzzleState() runs *after* the getLibraryPuzzle round-trip, so for
    // the whole request `status` still describes the previous puzzle while
    // puzzleId is already the new one. Without this identity check the effects
    // below fire for the sibling — requesting its diagnosis, which names the
    // best move, with no attempt made. `puzzle` only becomes the new one once
    // its fetch succeeds, so this is false for exactly the window in question.
    // No `readyFor === puzzleId` conjunct here on purpose. It looks like it
    // belongs, but the render-time reset above makes it unreachable: the pass
    // where `puzzle` is new and `status` still old is discarded by React before
    // commit, so no effect ever observes it. Verified -- removing the conjunct
    // changes no test, while deferring the reset to an effect fails four. A
    // condition that cannot change an outcome is not a safety net, it is noise
    // that makes the real guarantee harder to find.
    const resolved =
        puzzle?.id === puzzleId &&
        (status === 'correct' || status === 'incorrect' || status === 'revealed');

    useEffect(() => {
        if (!resolved || !username || !puzzleId || diagnosis) return;
        let stale = false;
        setDiagnosisLoading(true);
        getPuzzleDiagnosis(puzzleId, username, true)
            .then((result) => {
                if (!stale) setDiagnosis(result);
            })
            // Supplementary content: the page works without it, so a failure
            // stays silent rather than turning a solved puzzle into an error.
            .catch(() => undefined)
            .finally(() => {
                if (!stale) setDiagnosisLoading(false);
            });
        return () => {
            stale = true;
        };
    }, [resolved, username, puzzleId, diagnosis]);

    useEffect(() => {
        if (!resolved || !username || !puzzleId || similar) return;
        let stale = false;
        getSimilarPuzzles(puzzleId, username)
            .then((result) => {
                if (!stale) setSimilar(result);
            })
            // Supplementary, like the diagnosis: a failure leaves the section
            // unrendered rather than failing a solved puzzle.
            .catch(() => undefined);
        return () => {
            stale = true;
        };
    }, [resolved, username, puzzleId, similar]);

    const handleRecordResult = async (result: 'pass' | 'fail') => {
        if (!puzzle || !username || isRecording) return;
        setIsRecording(true);
        setRecordError(null);
        const elapsed = solveStartRef.current > 0 ? Date.now() - solveStartRef.current : undefined;
        if (elapsed) setSolveTimeMs(elapsed);
        try {
            const res = await reviewPuzzle(puzzle.id, username, result, elapsed);
            setRecorded(true);
            setNextDueAt(res.next_due_at);
            setFeedback(res.feedback);
        } catch (err) {
            console.error('Failed to record result:', err);
            setRecordError(err instanceof Error ? err.message : 'Failed to save your result. Please try again.');
        } finally {
            setIsRecording(false);
        }
    };

    const onPieceDrop = (sourceSquare: string, targetSquare: string, promotion: string = 'q') => {
        if (!puzzle || status === 'correct' || status === 'revealed') return false;
        try {
            const move = game.move({ from: sourceSquare, to: targetSquare, promotion: promotion || 'q' });
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
        } catch (e) {
            console.error('Failed to make move on board:', e);
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

    const handleMarkFailedRetry = () => {
        if (!puzzle) return;
        // Don't record a failure here — only record when the user reveals the
        // solution.  Recording on retry would double-count attempts.
        setStatus('solving');
        setUserMove('');
        setGame(new Chess(puzzle.fen));
        solveStartRef.current = Date.now();
    };

    // Before the loading branch, and in place rather than redirecting -- the
    // convention Library.tsx follows for the same reason (#319).
    //
    // This page previously had no no-username branch at all: the fetch returned
    // early *before* setting the loading flag, so `isLoading` kept its initial
    // `true` and the page showed "Loading puzzle..." forever. Making the fetch
    // conditional surfaces that state properly instead of hanging on it.
    if (!username) {
        return (
            <div className="space-y-12 animate-teedin">
                <section>
                    <Link to="/library" className="text-primary/70 hover:text-primary mb-4 inline-block font-sans text-sm tracking-widest uppercase transition-colors">
                        ← Back to Library
                    </Link>
                    <h1 className="text-3xl md:text-4xl font-serif text-primary">Puzzle</h1>
                </section>
                <ConnectAccountEmpty description="Puzzles are generated from your own games. Connect your Chess.com account to see this one." />
            </div>
        );
    }

    if (isLoading) {
        // The real title isn't known until the fetch lands, so use the same
        // 'Puzzle' fallback the loaded view uses. The h1 has to be here at all:
        // returning a bare status block left the page with no level-one heading,
        // so heading navigation gave no clue which page was loading.
        return (
            <div className="space-y-12 animate-teedin">
                <section>
                    <Link to="/library" className="text-primary/70 hover:text-primary mb-4 inline-block font-sans text-sm tracking-widest uppercase transition-colors">
                        ← Back to Library
                    </Link>
                    <h1 className="text-3xl md:text-4xl font-serif text-primary">Puzzle</h1>
                </section>
                <div className="text-center text-primary/70 py-12" role="status" aria-live="polite">
                    <span className="animate-pulse font-sans">Loading puzzle...</span>
                </div>
            </div>
        );
    }

    if (error || !puzzle) {
        // A 404 is terminal — retrying refetches the same missing id — so only a
        // transient error (500/network) gets a Retry, matching the other pages'
        // DataStateError affordance. "Back to Library" is always the escape hatch.
        const notFound = error === 'Puzzle not found' || (!error && !puzzle);
        return (
            <div className="space-y-12 animate-teedin">
                <section>
                    <Link to="/library" className="text-primary/70 hover:text-primary mb-4 inline-block font-sans text-sm tracking-widest uppercase transition-colors">
                        ← Back to Library
                    </Link>
                    <h1 className="text-3xl md:text-4xl font-serif text-primary mb-4">Puzzle</h1>
                    {notFound ? (
                        <div className="bg-red-500/10 border border-red-500/20 rounded-sm p-6 text-center" role="alert">
                            <p className="text-negative font-sans">{error || 'Puzzle not found'}</p>
                        </div>
                    ) : !online ? (
                        // A failed load while the browser is offline is a connectivity
                        // problem, not a server error — say so instead of a bare message.
                        <DataStateOffline onRetry={fetchPuzzle} compact />
                    ) : (
                        <DataStateError
                            message={error!}
                            onRetry={fetchPuzzle}
                            retryLabel="Retry"
                            ariaLabel="Retry loading this puzzle"
                            compact
                        />
                    )}
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
                <Link to="/library" className="text-primary/70 hover:text-primary mb-4 inline-block font-sans text-sm tracking-widest uppercase transition-colors">
                    ← Back to Library
                </Link>
                <h1 className="text-3xl md:text-4xl font-serif text-primary">
                    {puzzle.title || 'Puzzle'}
                </h1>
                <p className="mt-2 text-sm font-sans text-primary/70">
                    Exploration mode — the solution is shown on request and results here
                    are not counted as verified training. For a scored session, use{' '}
                    <Link to="/puzzles" className="km-inline-link km-focus-visible text-primary">
                        Train
                    </Link>.
                </p>
            </section>

            {/* Metadata strip */}
            <section className="flex flex-wrap gap-4 text-sm font-sans text-primary/70">
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
                    <span className="px-3 py-1 bg-red-500/10 rounded-sm border border-red-500/20 text-negative">
                        {puzzle.fail_count} failed
                    </span>
                )}
                {puzzle.last_reviewed_at && (
                    <span className="px-3 py-1 bg-primary/5 rounded-sm border border-primary/10">
                        Last: {new Date(puzzle.last_reviewed_at).toLocaleDateString(LOCALE)}
                    </span>
                )}
                {puzzle.next_due_at && !recorded && (
                    <span className="px-3 py-1 bg-primary/5 rounded-sm border border-primary/10">
                        Due: {new Date(puzzle.next_due_at).toLocaleDateString(LOCALE)}
                    </span>
                )}
            </section>

            {/* Board + Controls */}
            <section className="grid lg:grid-cols-2 gap-12 lg:gap-24">
                {/* Chessboard */}
                <div className="order-2 lg:order-1">
                    <div className="aspect-square w-full max-w-[600px] mx-auto shadow-2xl shadow-primary/5 rounded-sm overflow-hidden border border-primary/10">
                        <AccessibleChessboard
                            onKeyboardMove={({ sourceSquare, targetSquare, promotion }) =>
                                onPieceDrop(sourceSquare, targetSquare, promotion ?? 'q')
                            }
                            options={{
                                position: game.fen(),
                                onPieceDrop: ({ sourceSquare, targetSquare }) =>
                                    targetSquare ? onPieceDrop(sourceSquare, targetSquare) : false,
                                boardOrientation: puzzle.side_to_move === 'white' ? 'white' : 'black',
                                darkSquareStyle: { backgroundColor: 'var(--color-chess-board-dark)' },
                                lightSquareStyle: { backgroundColor: 'var(--color-chess-cream-300)' },
                            }}
                        />
                    </div>
                </div>

                {/* Sidebar Controls */}
                <div className="order-1 lg:order-2 space-y-8 flex flex-col justify-center">
                    {/* Side to move */}
                    <div className="bg-primary/5 p-4 rounded-sm border-l-2 border-primary">
                        <span className="font-sans text-sm tracking-wide uppercase text-primary/70">
                            {puzzle.side_to_move === 'white' ? 'White to Move' : 'Black to Move'}
                        </span>
                    </div>

                    {/* Status feedback */}
                    <div className="min-h-[80px] flex items-center justify-center text-center p-6 border border-primary/10 rounded-sm" role="status" aria-live="polite">
                        {status === 'solving' && (
                            <p className="text-primary/70 font-serif text-lg italic">Find the best move...</p>
                        )}
                        {status === 'correct' && (
                            <div className="text-center">
                                <p className="text-positive font-serif text-2xl animate-teedin">Correct!</p>
                                {feedback && <p className="text-positive font-sans text-sm mt-2">{feedback}</p>}
                            </div>
                        )}
                        {status === 'incorrect' && (
                            <p className="text-negative font-serif text-2xl animate-teedin">Incorrect.</p>
                        )}
                        {status === 'revealed' && (
                            <div>
                                <p className="text-primary/70 font-sans text-xs uppercase tracking-widest mb-1">Solution</p>
                                <p className="text-primary font-mono text-xl">{puzzle.best_move_uci}</p>
                            </div>
                        )}
                    </div>

                    {/* Recorded confirmation */}
                    {recorded && (
                        <div className="bg-green-500/10 border border-green-500/20 rounded-sm p-4 text-center animate-teedin">
                            <p className="text-positive font-serif font-medium">Recorded</p>
                            <div className="flex items-center justify-center gap-4 mt-2 text-sm font-sans text-positive">
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
                                <p className="text-positive font-sans text-sm mt-1">
                                    Next review: {new Date(nextDueAt).toLocaleDateString(LOCALE, {
                                        weekday: 'long', month: 'short', day: 'numeric'
                                    })}
                                </p>
                            )}
                        </div>
                    )}

                    {/* Record error */}
                    {recordError && (
                        <div className="bg-red-500/10 border border-red-500/20 rounded-sm p-4 text-center animate-teedin">
                            <p className="text-negative font-sans text-sm">{recordError}</p>
                        </div>
                    )}

                    {/* Actions */}
                    <div className="space-y-4">
                        {/* Manual UCI input toggle */}
                        <div className="flex justify-between items-center px-2">
                            <span className="text-xs text-primary/70 uppercase tracking-widest font-sans">Input Method</span>
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
                                    aria-label="Your move in coordinate notation, for example e2e4"
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
                                    className={`px-6 py-4 bg-primary text-bg-primary rounded-sm font-serif text-lg transition-all shadow-lg shadow-primary/5 km-focus-visible ${!userMove ? 'km-interactive-disabled' : 'km-interactive'}`}
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

                    <MistakeDiagnosisCard
                        diagnosis={diagnosis}
                        revealed={resolved}
                        loading={diagnosisLoading}
                    />

                    {/* Sits below the diagnosis on purpose: it only means
                        something once the user knows what went wrong here. */}
                    {resolved && (
                        <SimilarWeaknessCard data={similar} currentPuzzleId={puzzle.id} />
                    )}
                </div>
            </section>
        </div>
    );
}
