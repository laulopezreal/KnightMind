import { useState, useEffect } from 'react';
import { Link } from 'react-router-dom';
import { Chessboard } from 'react-chessboard';
import { Chess } from 'chess.js';
import { generatePuzzles, getDailyPuzzles, getUsers, ApiError, type Puzzle } from '../api/client';

type PuzzleStatus = 'solving' | 'correct' | 'incorrect' | 'revealed';

export default function Puzzles() {
    const [username, setUsername] = useState('');
    const [availableUsers, setAvailableUsers] = useState<string[]>([]);
    const [puzzles, setPuzzles] = useState<Puzzle[]>([]);
    const [currentIndex, setCurrentIndex] = useState(0);
    const [userMove, setUserMove] = useState('');
    const [showSuggestions, setShowSuggestions] = useState(false);
    const [status, setStatus] = useState<PuzzleStatus>('solving');
    const [isGenerating, setIsGenerating] = useState(false);
    const [isLoading, setIsLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);

    useEffect(() => {
        getUsers().then(setAvailableUsers).catch(console.error);
    }, []);

    const currentPuzzle = puzzles[currentIndex];

    const handleGeneratePuzzles = async () => {
        if (!username.trim()) {
            setError('Please enter a username');
            return;
        }

        setIsGenerating(true);
        setError(null);

        try {
            await generatePuzzles(username.trim());

            // After generation, fetch the daily puzzles
            const dailyPuzzles = await getDailyPuzzles(username.trim(), 5);
            setPuzzles(dailyPuzzles.puzzles);
            setCurrentIndex(0);
            setStatus('solving');
            setUserMove('');

            setError(null);
        } catch (err) {
            if (err instanceof ApiError) {
                if (err.statusCode === 404) {
                    setError('No games found. Please import games first from the Home page.');
                } else {
                    setError(err.detail || err.message);
                }
            } else {
                setError(err instanceof Error ? err.message : 'Failed to generate puzzles');
            }
        } finally {
            setIsGenerating(false);
        }
    };

    const handleLoadPuzzles = async () => {
        if (!username.trim()) {
            setError('Please enter a username');
            return;
        }

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

    const handleCheckAnswer = () => {
        if (!currentPuzzle) return;

        const normalizedUserMove = userMove.trim().toLowerCase();
        const normalizedBestMove = currentPuzzle.best_move_uci.toLowerCase();

        if (normalizedUserMove === normalizedBestMove) {
            setStatus('correct');
        } else {
            setStatus('incorrect');
        }
    };

    const handleRevealSolution = () => {
        setStatus('revealed');
        setUserMove(currentPuzzle?.best_move_uci || '');
    };

    const [game, setGame] = useState(new Chess());

    useEffect(() => {
        if (currentPuzzle) {
            setGame(new Chess(currentPuzzle.fen));
        }
    }, [currentPuzzle]);

    const onPieceDrop = (sourceSquare: string, targetSquare: string) => {
        if (!currentPuzzle || status === 'correct' || status === 'revealed') return false;

        try {
            const move = game.move({
                from: sourceSquare,
                to: targetSquare,
                promotion: 'q',
            });

            if (move === null) return false;

            setGame(new Chess(game.fen()));
            const uciMove = `${move.from}${move.to}${move.promotion || ''}`;
            setUserMove(uciMove);

            // Auto-check answer for drag-and-drop
            const normalizedBestMove = currentPuzzle.best_move_uci.toLowerCase();
            if (uciMove === normalizedBestMove) {
                setStatus('correct');
            } else {
                setStatus('incorrect');
                // Optional: Reset board after short delay or keep incorrect move? 
                // Usually puzzles strictly reject incorrect moves or show them as red.
                // Let's keep it simple: update status, user can try again.
                // To allow retry from current position, we might need to undo if incorrect?
                // Standard puzzle behavior: if incorrect, piece snaps back or stays with distinct error.
                // 'react-chessboard' snaps back if we access `return false` on drop, but we returned true (valid chess move).
                // If incorrect puzzle move, we might want to undo it in the game state so they can try again from the puzzle start?
                // For now, let's leave the piece there so they see what they played.
            }
            return true;
        } catch {
            return false;
        }
    };



    const handleNextPuzzle = () => {
        if (currentIndex < puzzles.length - 1) {
            setCurrentIndex(currentIndex + 1);
            setStatus('solving');
            setUserMove('');
            // game state will update via useEffect
        }
    };

    const formatEval = (evalScore: number) => {
        return evalScore > 0 ? `+${evalScore.toFixed(2)}` : evalScore.toFixed(2);
    };

    // Filter users based on input
    const filteredUsers = availableUsers.filter(user =>
        user.toLowerCase().includes(username.toLowerCase()) &&
        user.toLowerCase() !== username.toLowerCase()
    );

    console.log('State:', { availableUsers, username, showSuggestions, filteredUsers });

    return (
        <div className="min-h-screen bg-gradient-to-br from-slate-900 via-purple-900 to-slate-900">
            <div className="container mx-auto px-4 py-8">
                {/* Header */}
                <div className="mb-8">
                    <Link to="/" className="text-purple-400 hover:text-purple-300 mb-4 inline-block">
                        ← Back to Home
                    </Link>
                    <h1 className="text-4xl font-bold text-white mb-2">Daily Puzzles</h1>
                    <p className="text-gray-300">Solve tactical puzzles from your games</p>
                </div>

                {/* Username Input & Controls */}
                <div className="bg-white/10 backdrop-blur-md rounded-lg p-6 mb-6">
                    <div className="flex flex-col sm:flex-row gap-4 relative">
                        <div className="flex-1 relative">
                            <input
                                type="text"
                                placeholder="Enter username"
                                value={username}
                                onChange={(e) => {
                                    setUsername(e.target.value);
                                    setShowSuggestions(true);
                                }}
                                onFocus={() => setShowSuggestions(true)}
                                onBlur={() => setTimeout(() => setShowSuggestions(false), 200)}
                                className="w-full px-4 py-2 bg-white/20 border border-white/30 rounded-lg text-white placeholder-gray-400 focus:outline-none focus:ring-2 focus:ring-purple-500"
                                onKeyPress={(e) => e.key === 'Enter' && handleLoadPuzzles()}
                            />
                            {/* Custom Autocomplete Dropdown */}
                            {showSuggestions && username && filteredUsers.length > 0 && (
                                <div className="absolute z-10 w-full mt-1 bg-slate-800 border border-purple-500/30 rounded-lg shadow-xl overflow-hidden backdrop-blur-xl">
                                    {filteredUsers.map(user => (
                                        <div
                                            key={user}
                                            onClick={() => {
                                                setUsername(user);
                                                setShowSuggestions(false);
                                            }}
                                            className="px-4 py-2 text-gray-200 hover:bg-purple-600/50 cursor-pointer transition-colors"
                                        >
                                            {user}
                                        </div>
                                    ))}
                                </div>
                            )}
                        </div>
                        <button
                            onClick={handleLoadPuzzles}
                            disabled={isLoading || isGenerating}
                            className="px-6 py-2 bg-purple-600 hover:bg-purple-700 disabled:bg-gray-600 text-white rounded-lg font-medium transition-colors"
                        >
                            {isLoading ? 'Loading...' : 'Load Puzzles'}
                        </button>
                        <button
                            onClick={handleGeneratePuzzles}
                            disabled={isGenerating || isLoading}
                            className="px-6 py-2 bg-green-600 hover:bg-green-700 disabled:bg-gray-600 text-white rounded-lg font-medium transition-colors"
                        >
                            {isGenerating ? 'Generating...' : 'Generate New'}
                        </button>
                    </div>

                    {error && (
                        <div className="mt-4 p-4 bg-red-500/20 border border-red-500/50 rounded-lg text-red-200">
                            {error}
                        </div>
                    )}
                </div>

                {/* Puzzle Display */}
                {currentPuzzle && (
                    <div className="grid lg:grid-cols-2 gap-6">
                        {/* Chessboard */}
                        <div className="bg-white/10 backdrop-blur-md rounded-lg p-6">
                            <div className="mb-4 flex justify-between items-center">
                                <h2 className="text-xl font-semibold text-white">
                                    Puzzle {currentIndex + 1} of {puzzles.length}
                                </h2>
                                <div className="text-sm text-gray-300">
                                    {currentPuzzle.side_to_move === 'white' ? '⚪' : '⚫'} to move
                                </div>
                            </div>

                            <div className="mb-4">
                                <Chessboard
                                    options={{
                                        position: game.fen(),
                                        onPieceDrop: ({ sourceSquare, targetSquare }) => onPieceDrop(sourceSquare, targetSquare || ""),
                                        boardOrientation: currentPuzzle.side_to_move === 'white' ? 'white' : 'black',
                                        darkSquareStyle: { backgroundColor: '#4a5568' },
                                        lightSquareStyle: { backgroundColor: '#a0aec0' },
                                    }}
                                />
                            </div>

                            <div className="grid grid-cols-2 gap-4 text-sm">
                                <div className="bg-white/5 rounded p-3">
                                    <div className="text-gray-400 mb-1">Before</div>
                                    <div className="text-white font-mono">{formatEval(currentPuzzle.eval_before)}</div>
                                </div>
                                <div className="bg-white/5 rounded p-3">
                                    <div className="text-gray-400 mb-1">After</div>
                                    <div className="text-white font-mono">{formatEval(currentPuzzle.eval_after)}</div>
                                </div>
                            </div>
                        </div>

                        {/* Controls */}
                        <div className="bg-white/10 backdrop-blur-md rounded-lg p-6">
                            <h3 className="text-lg font-semibold text-white mb-4">Your Move</h3>

                            <div className="mb-6">
                                <label className="block text-sm text-gray-300 mb-2">
                                    Enter move in UCI format (e.g., e2e4, g1f3)
                                </label>
                                <input
                                    type="text"
                                    placeholder="e2e4"
                                    value={userMove}
                                    onChange={(e) => setUserMove(e.target.value)}
                                    disabled={status === 'correct' || status === 'revealed'}
                                    className="w-full px-4 py-3 bg-white/20 border border-white/30 rounded-lg text-white placeholder-gray-400 focus:outline-none focus:ring-2 focus:ring-purple-500 disabled:opacity-50 font-mono"
                                    onKeyPress={(e) => e.key === 'Enter' && status === 'solving' && handleCheckAnswer()}
                                />
                            </div>

                            {/* Status Messages */}
                            {status === 'correct' && (
                                <div className="mb-6 p-4 bg-green-500/20 border border-green-500/50 rounded-lg text-green-200">
                                    ✓ Correct! Well done!
                                </div>
                            )}

                            {status === 'incorrect' && (
                                <div className="mb-6 p-4 bg-red-500/20 border border-red-500/50 rounded-lg text-red-200">
                                    ✗ Incorrect. Try again or reveal the solution.
                                </div>
                            )}

                            {status === 'revealed' && (
                                <div className="mb-6 p-4 bg-blue-500/20 border border-blue-500/50 rounded-lg">
                                    <div className="text-blue-200 mb-2">Solution:</div>
                                    <div className="text-white font-mono text-lg">{currentPuzzle.best_move_uci}</div>
                                </div>
                            )}

                            {/* Action Buttons */}
                            <div className="flex flex-col gap-3">
                                {status === 'solving' && (
                                    <>
                                        <button
                                            onClick={handleCheckAnswer}
                                            disabled={!userMove.trim()}
                                            className="w-full px-6 py-3 bg-purple-600 hover:bg-purple-700 disabled:bg-gray-600 text-white rounded-lg font-medium transition-colors"
                                        >
                                            Check Answer
                                        </button>
                                        <button
                                            onClick={handleRevealSolution}
                                            className="w-full px-6 py-3 bg-gray-600 hover:bg-gray-700 text-white rounded-lg font-medium transition-colors"
                                        >
                                            Reveal Solution
                                        </button>
                                    </>
                                )}

                                {(status === 'correct' || status === 'revealed') && (
                                    <button
                                        onClick={handleNextPuzzle}
                                        disabled={currentIndex >= puzzles.length - 1}
                                        className="w-full px-6 py-3 bg-green-600 hover:bg-green-700 disabled:bg-gray-600 text-white rounded-lg font-medium transition-colors"
                                    >
                                        {currentIndex >= puzzles.length - 1 ? 'All Done!' : 'Next Puzzle →'}
                                    </button>
                                )}

                                {status === 'incorrect' && (
                                    <button
                                        onClick={() => {
                                            setStatus('solving');
                                            setUserMove('');
                                            if (currentPuzzle) {
                                                setGame(new Chess(currentPuzzle.fen));
                                            }
                                        }}
                                        className="w-full px-6 py-3 bg-purple-600 hover:bg-purple-700 text-white rounded-lg font-medium transition-colors"
                                    >
                                        Try Again
                                    </button>
                                )}
                            </div>

                            {/* Puzzle Info */}
                            <div className="mt-6 pt-6 border-t border-white/20">
                                <div className="text-sm text-gray-400 space-y-2">
                                    <div>Swing: <span className="text-white font-mono">{currentPuzzle.swing.toFixed(2)}</span></div>
                                    <div>From game: <span className="text-white font-mono">{currentPuzzle.source_game_id}</span></div>
                                </div>
                            </div>
                        </div>
                    </div>
                )}

                {/* Empty State */}
                {!currentPuzzle && !error && (
                    <div className="bg-white/10 backdrop-blur-md rounded-lg p-12 text-center">
                        <div className="text-6xl mb-4">🧩</div>
                        <h3 className="text-2xl font-semibold text-white mb-2">No Puzzles Loaded</h3>
                        <p className="text-gray-300 mb-6">
                            Enter your username and load puzzles to get started!
                        </p>
                    </div>
                )}
            </div>
        </div >
    );
}
