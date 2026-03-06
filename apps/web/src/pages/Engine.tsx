import { useState, useEffect, useRef, useCallback } from 'react';
import { Link } from 'react-router-dom';
import { Chessboard } from 'react-chessboard';
import { Chess } from 'chess.js';
import { evaluateFen, getEngineStatus, ApiError } from '../api';
import { useClue } from '../hooks/useClue';
import { PageHeader } from '../components/PageHeader';
import { DataStateError, DataStateLoading } from '../components/DataState';

const STARTING_FEN = 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1';

export default function Engine() {
  const [fen, setFen] = useState(STARTING_FEN);
  const [fenInput, setFenInput] = useState(STARTING_FEN);
  const [evaluation, setEvaluation] = useState<{ bestMove: string; eval: number } | null>(null);
  const [loading, setLoading] = useState(false);
  const [fenError, setFenError] = useState<string | null>(null);
  const [evaluationError, setEvaluationError] = useState<string | null>(null);
  const [engineAvailable, setEngineAvailable] = useState<boolean | null>(null);
  const [showBestMove, setShowBestMove] = useState(false);
  const [fenHistory, setFenHistory] = useState([STARTING_FEN]);
  const [historyIndex, setHistoryIndex] = useState(0);
  const lastEvaluatedFen = useRef<string | null>(null);
  const autoEvalTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const isMountedRef = useRef(true);
  const clue = useClue(evaluation?.bestMove ?? '', fen);
  const clueReset = clue.reset;

  useEffect(() => {
    isMountedRef.current = true;
    return () => { isMountedRef.current = false; };
  }, []);

  useEffect(() => {
    getEngineStatus().then(s => setEngineAvailable(s.available)).catch(() => setEngineAvailable(false));
  }, []);

  // Don't auto-evaluate on initial load so we show "Waiting for position"
  useEffect(() => {
    lastEvaluatedFen.current = STARTING_FEN;
  }, []);

  const handleEvaluate = useCallback(async () => {
    setLoading(true);
    setFenError(null);
    setEvaluationError(null);
    setEvaluation(null);
    setShowBestMove(false);
    clueReset();

    try {
      const result = await evaluateFen(fen);
      if (!isMountedRef.current) return;
      setEvaluation({ bestMove: result.best_move_uci, eval: result.eval });
    } catch (err) {
      if (!isMountedRef.current) return;
      if (err instanceof ApiError) {
        setEvaluationError(err.detail || err.message);
      } else {
        setEvaluationError(err instanceof Error ? err.message : 'Evaluation failed');
      }
    } finally {
      lastEvaluatedFen.current = fen;
      if (isMountedRef.current) {
        setLoading(false);
      }
    }
  }, [fen, clueReset]);

  useEffect(() => {
    if (!engineAvailable) return;
    if (lastEvaluatedFen.current === fen) return;
    if (autoEvalTimeoutRef.current) {
      clearTimeout(autoEvalTimeoutRef.current);
    }
    autoEvalTimeoutRef.current = setTimeout(() => {
      handleEvaluate();
    }, 500);
    return () => {
      if (autoEvalTimeoutRef.current) {
        clearTimeout(autoEvalTimeoutRef.current);
      }
    };
  }, [engineAvailable, fen, handleEvaluate]);

  const handleReset = () => {
    setFen(STARTING_FEN);
    setFenInput(STARTING_FEN);
    setEvaluation(null);
    setShowBestMove(false);
    setFenError(null);
    setEvaluationError(null);
    clueReset();
    setFenHistory([STARTING_FEN]);
    setHistoryIndex(0);
    lastEvaluatedFen.current = STARTING_FEN;
  };

  const handleFenSubmit = () => {
    try {
      new Chess(fenInput);
      setFen(fenInput);
      setLoading(true);
      setEvaluation(null);
      setShowBestMove(false);
      setFenError(null);
      setEvaluationError(null);
      clueReset();
      const nextHistory = [...fenHistory.slice(0, historyIndex + 1), fenInput];
      setFenHistory(nextHistory);
      setHistoryIndex(nextHistory.length - 1);
    } catch {
      setFenError('Invalid FEN string');
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
      setLoading(true);
      setEvaluation(null);
      setShowBestMove(false);
      setFenError(null);
      setEvaluationError(null);
      clueReset();
      const nextHistory = [...fenHistory.slice(0, historyIndex + 1), newFen];
      setFenHistory(nextHistory);
      setHistoryIndex(nextHistory.length - 1);
      return true;
    } catch { return false; }
  };

  const handleBack = () => {
    if (historyIndex <= 0) return;
    const nextIndex = historyIndex - 1;
    const previousFen = fenHistory[nextIndex];
    setHistoryIndex(nextIndex);
    setFen(previousFen);
    setFenInput(previousFen);
    setEvaluation(null);
    setShowBestMove(false);
    setFenError(null);
    setEvaluationError(null);
    clueReset();
  };

  const handleForward = () => {
    if (historyIndex >= fenHistory.length - 1) return;
    const nextIndex = historyIndex + 1;
    const nextFen = fenHistory[nextIndex];
    setHistoryIndex(nextIndex);
    setFen(nextFen);
    setFenInput(nextFen);
    setEvaluation(null);
    setShowBestMove(false);
    setFenError(null);
    setEvaluationError(null);
    clueReset();
  };

  const formatEval = (v: number) => {
    if (v >= 100) return 'M+'; if (v <= -100) return 'M-';
    return (v >= 0 ? '+' : '') + v.toFixed(2);
  };

  const getEvalColor = (v: number) => {
    if (v >= 1) return 'text-green-600'; if (v <= -1) return 'text-red-500';
    return 'text-primary';
  };

  const handleClue = () => {
    if (!evaluation?.bestMove) return;
    if (clue.isExhausted) {
      clueReset();
    } else {
      clue.advance();
    }
  };

  return (
    <div className="space-y-12 animate-teedin">
      <section className="space-y-6">
        <Link to="/" className="km-interactive km-focus-visible km-inline-link text-primary/40 inline-block font-sans text-sm tracking-widest uppercase transition-colors">
          ← Return Home
        </Link>
        <div className="relative bg-primary/5 border border-primary/10 rounded-sm p-8 lg:p-10">
          <div className="absolute top-8 right-8 lg:top-10 lg:right-10 flex items-center gap-2 rounded-full border border-primary/10 bg-primary/5 px-3 py-1.5 text-[10px] font-sans font-medium text-primary/60 uppercase tracking-widest">
            <span
              className={`h-2 w-2 rounded-full shrink-0 ${engineAvailable === null ? 'bg-primary/40 animate-pulse' : engineAvailable ? 'bg-green-500' : 'bg-red-500'}`}
            />
            {engineAvailable === null ? 'Checking' : engineAvailable ? 'Engine ready' : 'Engine offline'}
          </div>
          <div className="space-y-4 pr-32">
            <PageHeader
              title="Engine Analysis"
              subtitle="Evaluate any position and surface the best next move."
            />
            <p className="text-xs font-sans uppercase tracking-widest text-primary/40">
              Drag pieces or paste a FEN position
            </p>
          </div>
        </div>
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
                squareStyles: clue.squareStyles,
                boardOrientation: "white",
                darkSquareStyle: { backgroundColor: 'var(--color-chess-board-dark)' },
                lightSquareStyle: { backgroundColor: 'var(--color-chess-cream-300)' },
              }}
            />
          </div>
          <div className="mt-8 flex justify-center gap-3">
            <button
              type="button"
              onClick={handleBack}
              disabled={historyIndex <= 0}
              aria-label="Go back a position"
              title="Go back"
              className="km-interactive km-focus-visible border border-primary/20 px-4 py-2 text-primary/60 font-sans text-xs uppercase tracking-widest transition-colors rounded-sm disabled:opacity-40"
            >
              ←
            </button>
            <button
              type="button"
              onClick={handleForward}
              disabled={historyIndex >= fenHistory.length - 1}
              aria-label="Go forward a position"
              title="Go forward"
              className="km-interactive km-focus-visible border border-primary/20 px-4 py-2 text-primary/60 font-sans text-xs uppercase tracking-widest transition-colors rounded-sm disabled:opacity-40"
            >
              →
            </button>
            <button
              type="button"
              onClick={handleReset}
              className="km-interactive km-focus-visible px-6 py-2 text-primary/50 font-sans text-xs uppercase tracking-widest transition-colors rounded-sm"
            >
              Reset Position
            </button>
          </div>
        </div>

        {/* Controls */}
        <div className="order-1 lg:order-2 space-y-8 flex flex-col justify-center">

          {/* Evaluation */}
          <div className="bg-primary/5 border border-primary/10 rounded-sm p-8 space-y-6 min-h-[220px] flex flex-col">
            <div className="flex justify-between items-center border-b border-primary/10 pb-4">
              <span className="font-serif text-xl text-primary">Evaluation</span>
              {evaluation ? (
                <span className={`font-mono text-2xl ${getEvalColor(evaluation.eval)}`}>
                  {formatEval(evaluation.eval)}
                </span>
              ) : loading ? (
                <DataStateLoading label="Calculating..." compact />
              ) : (
                <span className="text-primary/40 font-serif italic">Waiting for position</span>
              )}
            </div>

            {evaluationError && (
              <DataStateError
                message={evaluationError}
                onRetry={handleEvaluate}
                retryLabel="Retry"
                ariaLabel="Retry evaluating position"
                compact
              />
            )}

            {evaluation ? (
              <div className="space-y-3 pt-2">
                <div className="flex justify-between items-center">
                  <span className="font-sans text-sm text-primary/60 uppercase tracking-widest">Best Move</span>
                  <div className="flex gap-4 items-center">
                    {showBestMove ? (
                      <span className="font-mono text-primary text-lg">{evaluation.bestMove}</span>
                    ) : (
                      <span className="text-primary/40 italic text-sm">Hidden</span>
                    )}
                    <button type="button" onClick={() => setShowBestMove(!showBestMove)} className="km-interactive km-focus-visible text-primary text-xs uppercase tracking-widest border border-primary/20 px-3 py-1 rounded-sm transition-colors">
                      {showBestMove ? 'Hide' : 'Show'}
                    </button>
                  </div>
                </div>
                <div className="flex flex-wrap items-center gap-3 text-xs font-sans text-primary/60">
                  <button
                    type="button"
                    onClick={handleClue}
                    className="km-interactive km-focus-visible border border-primary/20 px-3 py-1 text-[10px] font-serif uppercase tracking-widest text-primary transition-colors disabled:opacity-50"
                  >
                    {clue.clueStage === 0 ? 'Clue' : clue.clueStage === 1 ? 'Reveal squares' : 'Hide clues and reset'}
                  </button>
                  <span>
                    {clue.clueStage === 0
                      ? 'Tap for a small hint.'
                      : clue.clueStage === 1
                        ? clue.pieceHint
                        : ''}
                  </span>
                </div>
              </div>
            ) : (
              <p className="text-primary/50 font-sans text-sm">
                {loading ? 'Waiting for Stockfish output…' : 'Set or paste a position to analyze.'}
              </p>
            )}
          </div>

          {/* FEN */}
          <div className="space-y-2">
            <label className="block text-xs font-sans uppercase tracking-widest text-primary/40">Or paste FEN position</label>
            <div className="flex gap-4 border-b border-primary/20 pb-2 focus-within:border-primary/60 transition-colors">
              <input type="text" value={fenInput} onChange={(e) => setFenInput(e.target.value)}
                className="flex-1 bg-transparent border-none outline-none text-primary font-mono text-sm placeholder-primary/30"
              />
              <button type="button" onClick={handleFenSubmit} className="km-interactive km-focus-visible text-xs font-sans uppercase tracking-widest text-primary transition-colors">
                Load
              </button>
            </div>
            {fenError && <p className="text-red-500 text-xs font-sans">{fenError}</p>}
          </div>
        </div>
      </section>
    </div>
  );
}
