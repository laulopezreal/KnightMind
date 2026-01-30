import { useState, useEffect } from 'react';
import { Link } from 'react-router-dom';
import { Chessboard } from 'react-chessboard';
import { Chess } from 'chess.js';
import { generatePuzzles, getDailyPuzzles, getUsers, ApiError, type Puzzle } from '../api/client';
import { JobStatusCard } from '../components/JobStatusCard';
import { useJobPolling } from '../hooks/useJobPolling';

type PuzzleStatus = 'solving' | 'correct' | 'incorrect' | 'revealed';

export default function Puzzles() {
    const [username, setUsername] = useState('');
    const [availableUsers, setAvailableUsers] = useState<string[]>([]);
    const [puzzles, setPuzzles] = useState<Puzzle[]>([]);
    const [currentIndex, setCurrentIndex] = useState(0);
    const [userMove, setUserMove] = useState('');
    const [showSuggestions, setShowSuggestions] = useState(false);
    const [status, setStatus] = useState<PuzzleStatus>('solving');
    const [isLoading, setIsLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [showUciInput, setShowUciInput] = useState(false);
    const [activeJobId, setActiveJobId] = useState<string | null>(null);

    // Mock progress for now until we hook up real polling
    // const mockProgress = 0; 

    useEffect(() => {
        getUsers().then(setAvailableUsers).catch(console.error);
    }, []);

    const currentPuzzle = puzzles[currentIndex];

    // Load persisted job from local storage on mount or username change
    useEffect(() => {
        if (!username) return;
        const savedJobId = localStorage.getItem(`knightmind:lastJob:${username}`);
        if (savedJobId) {
            setActiveJobId(savedJobId);
        } else {
            setActiveJobId(null);
        }
    }, [username]);

    const { job, isPolling: isJobPolling } = useJobPolling(activeJobId, {
        enabled: !!activeJobId,
        onSuccess: () => {
            // Clear local storage on success so we don't start polling old finished jobs next time?
            // Or keep it to show "Success" state persistently until user generates new?
            // Prompt says: "If succeeded/failed, show final state and clear stored job_id (optional)"
            // Let's keep it to show the success card, but maybe trigger auto-refresh.

            // Auto-refresh puzzles
            getDailyPuzzles(username, 5).then((res) => {
                setPuzzles(res.puzzles);
                setCurrentIndex(0);
                setStatus('solving');
                setUserMove('');
            }).catch(console.error);

            // Clear job ID after a delay or let user clear it?
            // If we clear it immediately, the card disappears. We probably want the card to stay "Success".
            // We can clear localStorage but keep activeJobId in state for this session.
            localStorage.removeItem(`knightmind:lastJob:${username}`);
        },
        onError: () => {
            // Similarly clear storage on hard failure so we don't get stuck
            localStorage.removeItem(`knightmind:lastJob:${username}`);
        }
    });

    // Sync job status to local isGenerating for backwards compat with other UI if needed, 
    // but better to rely on 'job' object.

    // ... (keep logic same as original, just updating UI)
    // ... (keep logic same as original, just updating UI)
    const handleGeneratePuzzles = async () => {
        if (!username.trim()) {
            setError('Please enter a username');
            return;
        }
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
        }
    };

    const handleLoadPuzzles = async () => {
        if (!username.trim()) {
            setError('Please enter a username');
            return;
        }
        // Check if we already have a running job? Maybe not needed.
        setIsLoading(true);
        setError(null);

        try {
            const dailyPuzzles = await getDailyPuzzles(username.trim(), 5);
            setPuzzles(dailyPuzzles.puzzles);
            setCurrentIndex(0);
            setStatus('solving');
            setUserMove('');
            setError(null);
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
        } finally {
            setIsLoading(false);
        }
    };

    // Helper to determine active state
    const isGenerating = isJobPolling || (job?.status === 'queued' || job?.status === 'running');


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

    const [game, setGame] = useState(new Chess());

    useEffect(() => {
        if (currentPuzzle) setGame(new Chess(currentPuzzle.fen));
    }, [currentPuzzle]);

    const onPieceDrop = (sourceSquare: string, targetSquare: string) => {
        if (!currentPuzzle || status === 'correct' || status === 'revealed') return false;
        try {
            const move = game.move({ from: sourceSquare, to: targetSquare, promotion: 'q' });
            if (move === null) return false;
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
        }
    };

    const filteredUsers = availableUsers.filter(user =>
        user.toLowerCase().includes(username.toLowerCase()) &&
        user.toLowerCase() !== username.toLowerCase()
    );

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
                        <label className="block text-xs font-sans uppercase tracking-widest text-primary/40 mb-2">Username</label>
                        <input
                            type="text"
                            placeholder="Enter username"
                            value={username}
                            onChange={(e) => { setUsername(e.target.value); setShowSuggestions(true); }}
                            onFocus={() => setShowSuggestions(true)}
                            onBlur={() => setTimeout(() => setShowSuggestions(false), 200)}
                            className="w-full bg-transparent border-b border-primary/20 py-2 text-primary placeholder-primary/30 focus:outline-none focus:border-primary/60 transition-colors font-serif text-xl"
                            onKeyPress={(e) => e.key === 'Enter' && handleLoadPuzzles()}
                        />
                        {showSuggestions && username && filteredUsers.length > 0 && (
                            <div className="absolute z-10 w-full mt-1 bg-bg-primary border border-primary/20 rounded-sm shadow-xl max-h-48 overflow-y-auto">
                                {filteredUsers.map(user => (
                                    <div key={user} onClick={() => { setUsername(user); setShowSuggestions(false); }}
                                        className="px-4 py-2 text-primary hover:bg-primary/5 cursor-pointer transition-colors font-sans">
                                        {user}
                                    </div>
                                ))}
                            </div>
                        )}
                    </div>
                    <div className="flex gap-4">
                        <button onClick={handleLoadPuzzles} disabled={isLoading || isGenerating}
                            className="px-6 py-2 border border-primary/20 text-primary hover:bg-primary hover:text-bg-primary hover:border-transparent rounded-sm font-serif transition-all disabled:opacity-50">
                            {isLoading ? 'Loading...' : 'Load Puzzles'}
                        </button>
                        <button onClick={handleGeneratePuzzles} disabled={isGenerating || isLoading}
                            className="px-6 py-2 bg-primary text-bg-primary hover:opacity-90 rounded-sm font-serif transition-colors disabled:opacity-50">
                            {isGenerating ? 'Generating...' : 'Generate New'}
                        </button>
                    </div>
                </div>

                {/* Job Status / Error Area */}
                <div className="mt-6">
                    {error && !isGenerating && (
                        <JobStatusCard status="failed" error={error} />
                    )}
                    {job && (job.status === 'queued' || job.status === 'running' || job.status === 'succeeded' || job.status === 'failed') && (
                        <JobStatusCard
                            status={job.status}
                            message={job.message}
                            progress={job.progress}
                            error={job.status === 'failed' ? job.message : undefined}
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
                                    onPieceDrop: (sourceSquare, targetSquare) => onPieceDrop(sourceSquare, targetSquare),
                                    boardOrientation: currentPuzzle.side_to_move === 'white' ? 'white' : 'black',
                                    darkSquareStyle: { backgroundColor: 'var(--color-chess-brown-700)' },
                                    lightSquareStyle: { backgroundColor: 'var(--color-chess-cream-300)' },
                                }}
                            />
                        </div>
                    </div>

                    {/* Sidebar Controls */}
                    <div className="order-1 lg:order-2 space-y-8 flex flex-col justify-center">
                        <div className="space-y-2">
                            <div className="flex justify-between items-center bg-primary/5 p-4 rounded-sm border-l-2 border-primary">
                                <span className="font-serif text-xl text-primary">Puzzle {currentIndex + 1} / {puzzles.length}</span>
                                <span className="font-sans text-sm tracking-wide uppercase text-primary/60">
                                    {currentPuzzle.side_to_move === 'white' ? 'White to Move' : 'Black to Move'}
                                </span>
                            </div>
                        </div>

                        {/* Status Area */}
                        <div className="min-h-[100px] flex items-center justify-center text-center p-6 border border-primary/10 rounded-sm relative overflow-hidden">
                            {status === 'solving' && <p className="text-primary/60 font-serif text-lg italic">Find the best move...</p>}
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
                                    onClick={() => setShowUciInput(!showUciInput)}
                                    className="text-primary hover:text-primary/60 text-xs font-serif underline decoration-primary/30 underline-offset-4 transition-colors">
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
                                <div className="grid grid-cols-2 gap-4">
                                    <button
                                        onClick={handleCheckAnswer}
                                        disabled={!userMove}
                                        className="px-6 py-4 bg-primary text-bg-primary hover:opacity-90 rounded-sm font-serif text-lg transition-all disabled:opacity-50 shadow-lg shadow-primary/5">
                                        Check Move
                                    </button>
                                    <button
                                        onClick={handleRevealSolution}
                                        className="px-6 py-4 border border-primary/20 text-primary hover:bg-primary/5 rounded-sm font-serif text-lg transition-all">
                                        Reveal
                                    </button>
                                </div>
                            )}
                            {(status === 'correct' || status === 'revealed') && (
                                <button
                                    onClick={handleNextPuzzle}
                                    disabled={currentIndex >= puzzles.length - 1}
                                    className="w-full px-6 py-4 bg-green-600 text-white hover:bg-green-700 rounded-sm font-serif text-lg transition-all shadow-lg shadow-green-900/20">
                                    {currentIndex >= puzzles.length - 1 ? 'All Done' : 'Next Puzzle →'}
                                </button>
                            )}
                            {status === 'incorrect' && (
                                <button
                                    onClick={() => { setStatus('solving'); setUserMove(''); setGame(new Chess(currentPuzzle.fen)); }}
                                    className="w-full px-6 py-4 border border-primary/20 text-primary hover:bg-primary hover:text-bg-primary rounded-sm font-serif text-lg transition-all">
                                    Try Again
                                </button>
                            )}
                        </div>
                    </div>
                </section>
            )}
        </div>
    );
}
