import { useState, useMemo, useCallback } from 'react';
import { parseBestMoveUci, getPieceNameAtSquare } from '../utils/puzzle-clue';

export type ClueStage = 0 | 1 | 2 | 3;

const HIGHLIGHT_STYLE = { backgroundColor: 'rgba(255, 235, 59, 0.45)' };

export interface UseClueOptions {
    /**
     * Highest rung the ladder can climb to.
     *  - `2` (default): nudge → squares. Used by Engine analysis, where there
     *    is no separate "reveal the whole line" concept, so the top rung is
     *    the from/to highlight.
     *  - `3`: nudge → squares → full solution. Used by puzzle solving, where
     *    the third rung hands over the complete answer. The consumer is
     *    responsible for surfacing the solution text on that final rung.
     */
    maxStage?: 2 | 3;
}

export interface UseClueReturn {
    clueStage: ClueStage;
    squareStyles: Record<string, { backgroundColor: string }>;
    /** Rung 1 text, e.g. "Move the knight". Empty before rung 1. */
    pieceHint: string;
    /** Rung 2 text, e.g. "Move the knight to e5". Empty before rung 2. */
    moveHint: string;
    fromSquare: string;
    toSquare: string;
    /**
     * Climb one rung. No-ops when no move is known, UNLESS `force` is passed —
     * which the puzzle surface uses on the first press, where it has *just*
     * fetched the solution but this render's closure hasn't seen it yet.
     */
    advance: (force?: boolean) => void;
    reset: () => void;
    isExhausted: boolean;
    isDisabled: boolean;
    label: string;
}

/**
 * Shared clue state machine for the progressive hint ladder.
 *
 * The ladder is one-directional (advance / reset) and rung-for-rung identical
 * across the puzzle and engine surfaces — the only difference is how many rungs
 * the consumer opts into (see {@link UseClueOptions.maxStage}). Keeping the
 * rung logic here is what lets the puzzle "Hint (n/3)" control and the engine
 * "Clue" control stay honest with each other.
 *
 * @param bestMoveUci - UCI notation of the best move (e.g. "e2e4"), empty string if unavailable.
 * @param fen - Board FEN the move applies to, used to name the piece for the hint.
 * @param options - Ladder configuration; see {@link UseClueOptions}.
 */
export function useClue(bestMoveUci: string, fen: string, options: UseClueOptions = {}): UseClueReturn {
    const maxStage = options.maxStage ?? 2;
    const [clueStage, setClueStage] = useState<ClueStage>(0);

    const bestMoveParsed = useMemo(
        () => (bestMoveUci ? parseBestMoveUci(bestMoveUci) : { from: '', to: '' }),
        [bestMoveUci],
    );

    const squareStyles = useMemo<Record<string, { backgroundColor: string }>>(() => {
        if (clueStage < 1 || !bestMoveParsed.from) return {};
        // Rung 2 and beyond light up the destination too.
        if (clueStage >= 2 && bestMoveParsed.to) {
            return {
                [bestMoveParsed.from]: HIGHLIGHT_STYLE,
                [bestMoveParsed.to]: HIGHLIGHT_STYLE,
            };
        }
        return { [bestMoveParsed.from]: HIGHLIGHT_STYLE };
    }, [clueStage, bestMoveParsed]);

    const pieceHint = useMemo(
        () => (clueStage >= 1 ? getPieceNameAtSquare(fen, bestMoveParsed.from) : ''),
        [clueStage, fen, bestMoveParsed.from],
    );

    const moveHint = useMemo(() => {
        if (clueStage < 2) return '';
        const piece = getPieceNameAtSquare(fen, bestMoveParsed.from);
        return bestMoveParsed.to ? `${piece} to ${bestMoveParsed.to}` : piece;
    }, [clueStage, fen, bestMoveParsed.from, bestMoveParsed.to]);

    const advance = useCallback((force = false) => {
        if (!bestMoveUci && !force) return;
        setClueStage((prev) => (prev < maxStage ? ((prev + 1) as ClueStage) : prev));
    }, [bestMoveUci, maxStage]);

    const reset = useCallback(() => setClueStage(0), []);

    const isExhausted = clueStage >= maxStage;
    const isDisabled = !bestMoveUci || isExhausted;
    // Label is consumed by the 2-stage engine surface; the puzzle surface builds
    // its own "Hint (n/3)" label from clueStage.
    const label = clueStage === 0 ? 'Clue' : clueStage === 1 ? 'Reveal squares' : 'Clue used';

    return {
        clueStage,
        squareStyles,
        pieceHint,
        moveHint,
        fromSquare: bestMoveParsed.from,
        toSquare: bestMoveParsed.to ?? '',
        advance,
        reset,
        isExhausted,
        isDisabled,
        label,
    };
}
