import { useState, useEffect } from 'react';
import { Link } from 'react-router-dom';
import { Chessboard } from 'react-chessboard';
import { Chess } from 'chess.js';
import { evaluateFen, getEngineStatus, ApiError } from '../api/client';

const STARTING_FEN = 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1';

export default function Engine() {
  const [fen, setFen] = useState(STARTING_FEN);
  const [fenInput, setFenInput] = useState(STARTING_FEN);
  const [evaluation, setEvaluation] = useState<{ bestMove: string; eval: number } | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [engineAvailable, setEngineAvailable] = useState<boolean | null>(null);
  const [showBestMove, setShowBestMove] = useState(false);

  useEffect(() => {
    getEngineStatus().then(s => setEngineAvailable(s.available)).catch(() => setEngineAvailable(false));
  }, []);

  const handleEvaluate = async () => {
    setLoading(true);
    setError(null);
    setEvaluation(null);
    setShowBestMove(false);

    try {
      const result = await evaluateFen(fen);
      setEvaluation({ bestMove: result.best_move_uci, eval: result.eval });
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

  const handleReset = () => {
    setFen(STARTING_FEN);
    setFenInput(STARTING_FEN);
    setEvaluation(null);
    setShowBestMove(false);
    setError(null);
  };

  const handleFenSubmit = () => {
    try {
      new Chess(fenInput);
      setFen(fenInput);
      setEvaluation(null);
      setShowBestMove(false);
      setError(null);
    } catch {
      setError('Invalid FEN string');
    }
  };

  const onDrop = (sourceSquare: string, targetSquare: string) => {
    const gameCopy = new Chess(fen);
    try {
      const move = gameCopy.move({ from: sourceSquare, to: targetSquare, promotion: 'q' });
      if (move === null) return false;
      const newFen = gameCopy.fen();
      setFen(newFen);
      setFenInput(newFen);
      setEvaluation(null);
      setShowBestMove(false);
      return true;
    } catch { return false; }
  };

  const formatEval = (v: number) => {
    if (v >= 100) return 'M+'; if (v <= -100) return 'M-';
    return (v >= 0 ? '+' : '') + v.toFixed(2);
  };

  const getEvalColor = (v: number) => {
    if (v >= 1) return 'text-green-600'; if (v <= -1) return 'text-red-500';
    return 'text-primary';
  };

  return (
    <div className="space-y-12 animate-teedin">
      <section className="flex justify-between items-end">
        <div>
          <Link to="/" className="text-primary/40 hover:text-primary mb-4 inline-block font-sans text-sm tracking-widest uppercase transition-colors">
            ← Return Home
          </Link>
          <h1 className="text-4xl md:text-5xl font-serif text-primary mb-2">Engine Analysis</h1>
          <p className="text-lg text-primary/60 font-sans">Analyze positions with Stockfish.</p>
        </div>
        {engineAvailable && (
          <div className="flex items-center gap-2 px-4 py-2 bg-green-500/10 border border-green-500/20 rounded-full">
            <div className="w-2 h-2 rounded-full bg-green-500 animate-pulse" />
            <span className="text-green-600 text-xs font-sans font-medium uppercase tracking-wider">Engine Ready</span>
          </div>
        )}
      </section>

      <section className="grid lg:grid-cols-2 gap-12 lg:gap-24">
        {/* Board */}
        <div className="order-2 lg:order-1">
          <div className="aspect-square w-full max-w-[600px] mx-auto shadow-2xl shadow-primary/5 rounded-sm overflow-hidden border border-primary/10">
            <Chessboard
              options={{
                position: fen,
                onPieceDrop: ({ sourceSquare, targetSquare }) => targetSquare ? onDrop(sourceSquare, targetSquare) : false,
                arrows: showBestMove && evaluation ? [{ startSquare: evaluation.bestMove.slice(0, 2), endSquare: evaluation.bestMove.slice(2, 4), color: 'rgba(16, 185, 129, 0.8)' }] : [],
                boardOrientation: "white",
                darkSquareStyle: { backgroundColor: 'var(--color-chess-brown-700)' },
                lightSquareStyle: { backgroundColor: 'var(--color-chess-cream-300)' },
              }}
            />
          </div>
          <div className="mt-8 flex justify-center">
            <button onClick={handleReset} className="px-6 py-2 text-primary/60 hover:text-primary font-sans text-sm uppercase tracking-widest transition-colors">
              Reset Position
            </button>
          </div>
        </div>

        {/* Controls */}
        <div className="order-1 lg:order-2 space-y-8 flex flex-col justify-center">

          {/* FEN */}
          <div className="space-y-2">
            <label className="block text-xs font-sans uppercase tracking-widest text-primary/40">FEN Position</label>
            <div className="flex gap-4 border-b border-primary/20 pb-2 focus-within:border-primary/60 transition-colors">
              <input type="text" value={fenInput} onChange={(e) => setFenInput(e.target.value)}
                className="flex-1 bg-transparent border-none outline-none text-primary font-mono text-sm placeholder-primary/30"
              />
              <button onClick={handleFenSubmit} className="text-xs font-sans uppercase tracking-widest text-primary hover:text-primary/60 transition-colors">
                Load
              </button>
            </div>
            {error && <p className="text-red-500 text-xs font-sans">{error}</p>}
          </div>

          {/* Analysis Box */}
          <div className="bg-primary/5 border border-primary/10 rounded-sm p-8 space-y-6">
            <div className="flex justify-between items-center border-b border-primary/10 pb-4">
              <span className="font-serif text-xl text-primary">Evaluation</span>
              {evaluation ? (
                <span className={`font-mono text-2xl ${getEvalColor(evaluation.eval)}`}>
                  {formatEval(evaluation.eval)}
                </span>
              ) : (
                <span className="text-primary/40 font-serif italic">Pending...</span>
              )}
            </div>

            {evaluation && (
              <div className="flex justify-between items-center pt-2">
                <span className="font-sans text-sm text-primary/60 uppercase tracking-widest">Best Move</span>
                <div className="flex gap-4 items-center">
                  {showBestMove ? (
                    <span className="font-mono text-primary text-lg">{evaluation.bestMove}</span>
                  ) : (
                    <span className="text-primary/40 italic text-sm">Hidden</span>
                  )}
                  <button onClick={() => setShowBestMove(!showBestMove)} className="text-primary hover:text-primary/60 text-xs uppercase tracking-widest border border-primary/20 px-3 py-1 rounded-sm transition-colors">
                    {showBestMove ? 'Hide' : 'Show'}
                  </button>
                </div>
              </div>
            )}

            <button onClick={handleEvaluate} disabled={loading || !engineAvailable}
              className="w-full py-4 mt-4 bg-primary text-bg-primary hover:opacity-90 disabled:opacity-50 rounded-sm font-serif text-lg transition-all">
              {loading ? 'Analyzing...' : 'Evaluate Position'}
            </button>
          </div>
        </div>
      </section>
    </div>
  );
}
