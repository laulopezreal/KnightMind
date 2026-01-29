import { useState, useEffect } from 'react';
import { Link } from 'react-router-dom';
import { Chessboard } from 'react-chessboard';
import { Chess } from 'chess.js';
import { evaluateFen, getEngineStatus, ApiError } from '../api/client';

const STARTING_FEN = 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1';

export default function Engine() {
  const [game, setGame] = useState(new Chess());
  const [fen, setFen] = useState(STARTING_FEN);
  const [fenInput, setFenInput] = useState(STARTING_FEN);
  const [evaluation, setEvaluation] = useState<{ bestMove: string; eval: number } | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [engineAvailable, setEngineAvailable] = useState<boolean | null>(null);
  const [showBestMove, setShowBestMove] = useState(false);

  // Check engine status on mount
  useEffect(() => {
    getEngineStatus().then(status => {
      setEngineAvailable(status.available);
      if (!status.available) {
        setError(status.message);
      }
    }).catch(() => {
      setEngineAvailable(false);
      setError('Failed to check engine status');
    });
  }, []);

  const handleEvaluate = async () => {
    setLoading(true);
    setError(null);
    setEvaluation(null);
    setShowBestMove(false);

    try {
      const result = await evaluateFen(fen);
      setEvaluation({
        bestMove: result.best_move_uci,
        eval: result.eval,
      });
    } catch (err) {
      if (err instanceof ApiError) {
        setError(err.detail || err.message);
      } else {
        setError(err instanceof Error ? err.message : 'Evaluation failed');
      }
    } finally {
      setLoading(false);
    }
  };

  const handleFenSubmit = () => {
    try {
      const newGame = new Chess(fenInput);
      setGame(newGame);
      setFen(fenInput);
      setEvaluation(null);
      setError(null);
      setShowBestMove(false);
    } catch {
      setError('Invalid FEN string');
    }
  };

  const handleReset = () => {
    const newGame = new Chess();
    setGame(newGame);
    setFen(STARTING_FEN);
    setFenInput(STARTING_FEN);
    setEvaluation(null);
    setError(null);
    setShowBestMove(false);
  };

  const onDrop = (sourceSquare: string, targetSquare: string) => {
    try {
      const move = game.move({
        from: sourceSquare,
        to: targetSquare,
        promotion: 'q', // Always promote to queen for simplicity
      });

      if (move === null) return false;

      const newFen = game.fen();
      setFen(newFen);
      setFenInput(newFen);
      setEvaluation(null);
      setShowBestMove(false);
      return true;
    } catch {
      return false;
    }
  };

  // Convert UCI move to arrow for visualization
  const getArrows = () => {
    if (!showBestMove || !evaluation?.bestMove) return [];
    const startSquare = evaluation.bestMove.slice(0, 2);
    const endSquare = evaluation.bestMove.slice(2, 4);
    return [{ startSquare, endSquare, color: 'rgb(16, 185, 129)' }]; // Emerald color
  };

  const formatEval = (evalValue: number): string => {
    if (evalValue >= 100) return 'M+';
    if (evalValue <= -100) return 'M-';
    const sign = evalValue >= 0 ? '+' : '';
    return `${sign}${evalValue.toFixed(2)}`;
  };

  const getEvalColor = (evalValue: number): string => {
    if (evalValue >= 2) return 'text-green-400';
    if (evalValue >= 0.5) return 'text-lime-400';
    if (evalValue >= -0.5) return 'text-gray-300';
    if (evalValue >= -2) return 'text-orange-400';
    return 'text-red-400';
  };

  return (
    <div className="min-h-screen bg-gray-900 text-white">
      <nav className="bg-gray-800 p-4">
        <div className="container mx-auto flex gap-6">
          <Link to="/" className="text-xl font-bold text-emerald-400">KnightMind</Link>
          <Link to="/openings" className="hover:text-emerald-400">Openings</Link>
          <Link to="/engine" className="text-emerald-400">Engine</Link>
        </div>
      </nav>

      <main className="container mx-auto p-8">
        <h1 className="text-4xl font-bold mb-4">Engine Evaluation</h1>
        <p className="text-gray-400 mb-6">Test Stockfish position evaluation</p>

        {/* Engine status - only show when available */}
        {engineAvailable === true && (
          <div className="bg-emerald-900/30 border border-emerald-800 rounded-lg px-4 py-2 mb-6 inline-flex items-center gap-2">
            <div className="w-2 h-2 rounded-full bg-emerald-500"></div>
            <p className="text-emerald-400 text-sm">Stockfish ready</p>
          </div>
        )}

        <div className="grid grid-cols-1 lg:grid-cols-2 gap-8">
          {/* Chessboard */}
          <div className="bg-gray-800 rounded-lg p-6">
            <div className="max-w-md mx-auto">
              <Chessboard
                options={{
                  position: fen,
                  onPieceDrop: ({ sourceSquare, targetSquare }) => 
                    targetSquare ? onDrop(sourceSquare, targetSquare) : false,
                  arrows: getArrows(),
                  boardOrientation: 'white',
                  darkSquareStyle: { backgroundColor: '#4a5568' },
                  lightSquareStyle: { backgroundColor: '#a0aec0' },
                }}
              />
            </div>
            <div className="mt-4 flex justify-center gap-4">
              <button
                onClick={handleReset}
                className="px-4 py-2 bg-gray-700 hover:bg-gray-600 rounded transition-colors"
              >
                Reset Board
              </button>
            </div>
          </div>

          {/* Controls */}
          <div className="space-y-6">
            {/* FEN Input */}
            <div className="bg-gray-800 rounded-lg p-6">
              <h2 className="text-lg font-semibold mb-3">Position (FEN)</h2>
              <div className="flex gap-2">
                <input
                  type="text"
                  value={fenInput}
                  onChange={(e) => setFenInput(e.target.value)}
                  className="flex-1 px-3 py-2 bg-gray-700 border border-gray-600 rounded focus:border-emerald-400 focus:outline-none text-sm font-mono"
                />
                <button
                  onClick={handleFenSubmit}
                  className="px-4 py-2 bg-gray-600 hover:bg-gray-500 rounded transition-colors"
                >
                  Load
                </button>
              </div>
              <p className="text-xs text-gray-500 mt-2">
                {game.turn() === 'w' ? 'White' : 'Black'} to move
              </p>
            </div>

            {/* Evaluate */}
            <div className="bg-gray-800 rounded-lg p-6">
              <div className="flex items-center gap-2 mb-3">
                <h2 className="text-lg font-semibold">Evaluation</h2>
                {!engineAvailable && engineAvailable !== null && (
                  <div className="relative group">
                    <div className="w-5 h-5 rounded-full bg-amber-600 flex items-center justify-center cursor-help text-xs font-bold">
                      !
                    </div>
                    <div className="absolute left-0 bottom-full mb-2 w-64 p-3 bg-gray-900 border border-gray-600 rounded-lg shadow-xl opacity-0 invisible group-hover:opacity-100 group-hover:visible transition-all duration-200 z-20">
                      <p className="text-sm text-gray-300 mb-2">
                        <strong className="text-amber-400">Engine unavailable</strong>
                      </p>
                      <p className="text-xs text-gray-400">
                        Make sure the backend server is running and Stockfish is installed. 
                        See the README for setup instructions.
                      </p>
                      <div className="absolute left-3 -bottom-1 w-2 h-2 bg-gray-900 border-b border-r border-gray-600 transform rotate-45"></div>
                    </div>
                  </div>
                )}
              </div>
              <div className="relative">
                <button
                  onClick={handleEvaluate}
                  disabled={loading || !engineAvailable}
                  className="w-full px-6 py-3 bg-emerald-600 hover:bg-emerald-700 disabled:bg-gray-600 disabled:cursor-not-allowed rounded font-medium transition-colors"
                >
                  {loading ? 'Evaluating...' : 'Evaluate Position'}
                </button>
                {!engineAvailable && engineAvailable !== null && (
                  <p className="text-amber-400 text-xs mt-2 flex items-center gap-1">
                    <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
                    </svg>
                    Hover the icon above for details
                  </p>
                )}
              </div>

              {error && (
                <p className="text-red-400 mt-3 text-sm">{error}</p>
              )}

              {evaluation && (
                <div className="mt-4 space-y-3">
                  <div className="flex items-center justify-between p-3 bg-gray-700 rounded">
                    <span className="text-gray-400">Evaluation:</span>
                    <span className={`text-2xl font-bold ${getEvalColor(evaluation.eval)}`}>
                      {formatEval(evaluation.eval)}
                    </span>
                  </div>
                  
                  <div className="flex items-center justify-between p-3 bg-gray-700 rounded">
                    <span className="text-gray-400">Best Move:</span>
                    <div className="flex items-center gap-2">
                      {showBestMove ? (
                        <span className="text-lg font-mono text-emerald-400">{evaluation.bestMove}</span>
                      ) : (
                        <span className="text-gray-500">Hidden</span>
                      )}
                      <button
                        onClick={() => setShowBestMove(!showBestMove)}
                        className="px-3 py-1 text-sm bg-gray-600 hover:bg-gray-500 rounded transition-colors"
                      >
                        {showBestMove ? 'Hide' : 'Show'}
                      </button>
                    </div>
                  </div>

                  <p className="text-xs text-gray-500">
                    Positive = advantage for side to move
                  </p>
                </div>
              )}
            </div>

            {/* Instructions */}
            <div className="bg-gray-800 rounded-lg p-6 text-sm text-gray-400">
              <h3 className="font-semibold text-gray-300 mb-2">How to use:</h3>
              <ul className="list-disc list-inside space-y-1">
                <li>Drag pieces to make moves on the board</li>
                <li>Or paste a FEN string and click "Load"</li>
                <li>Click "Evaluate Position" to get Stockfish analysis</li>
                <li>Click "Show" to reveal the best move (shown as arrow)</li>
              </ul>
            </div>
          </div>
        </div>
      </main>
    </div>
  );
}
