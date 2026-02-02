import { useState, useMemo, useCallback } from 'react';
import { parseBestMoveUci, getPieceNameAtSquare } from '../utils/puzzle-clue';

type ClueStage = 0 | 1 | 2;

const HIGHLIGHT_STYLE = { backgroundColor: 'rgba(255, 235, 59, 0.45)' };

export interface UseClueReturn {
    clueStage: ClueStage;
    squareStyles: Record<string, { backgroundColor: string }>;
    pieceHint: string;
    advance: () => void;
    reset: () => void;
    isExhausted: boolean;
    isDisabled: boolean;
    label: string;
}

/**
 * Shared clue state machine for the 3-stage progressive hint system.
 *
 * @param bestMoveUci - UCI notation of the best move (e.g. "e2e4"), empty string if unavailable.
 * @param fen - Current board FEN, used to determine piece names for hints.
 */
export function useClue(bestMoveUci: string, fen: string): UseClueReturn {
    const [clueStage, setClueStage] = useState<ClueStage>(0);

    const bestMoveParsed = useMemo(
        () => (bestMoveUci ? parseBestMoveUci(bestMoveUci) : { from: '', to: '' }),
        [bestMoveUci],
    );

    const squareStyles = useMemo<Record<string, { backgroundColor: string }>>(() => {
        if (clueStage < 1 || !bestMoveParsed.from) return {};
        if (clueStage === 2 && bestMoveParsed.to) {
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

    const advance = useCallback(() => {
        if (!bestMoveUci) return;
        setClueStage((prev) => {
            if (prev === 0) return 1;
            if (prev === 1) return 2;
            return prev;
        });
    }, [bestMoveUci]);

    const reset = useCallback(() => setClueStage(0), []);

    const isExhausted = clueStage === 2;
    const isDisabled = !bestMoveUci || clueStage === 2;
    const label = clueStage === 0 ? 'Clue' : clueStage === 1 ? 'Reveal squares' : 'Clue used';

    return { clueStage, squareStyles, pieceHint, advance, reset, isExhausted, isDisabled, label };
}
