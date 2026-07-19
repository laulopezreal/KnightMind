import { useCallback, useEffect, useMemo, useRef, useState, type ComponentProps } from 'react';
import { Chessboard } from 'react-chessboard';

/**
 * An accessible, keyboard-operable control layer over react-chessboard.
 *
 * react-chessboard (v5, via @dnd-kit) renders pieces as draggable
 * `role="button"` elements with no accessible name and offers no keyboard way to
 * actually make a move. Rather than label those draggables (which leaves the
 * board perceivable but unplayable and duplicates the board in the a11y tree),
 * we hide the visual board from assistive tech and expose a real ARIA grid on
 * top of it:
 *
 *   - `role="grid"` with 8 `role="row"`s of 8 `role="gridcell"`s, each labelled
 *     with its square and occupant ("e4, white pawn" / "d5, empty").
 *   - Roving tabindex + arrow-key navigation (Home/End jump along a rank).
 *   - Enter/Space picks up the piece on the focused square, then places it on a
 *     second Enter/Space; Escape cancels the pick-up.
 *   - A pawn reaching the last rank opens an accessible promotion chooser
 *     (when the host wires `onKeyboardMove`, which can carry the promotion).
 *   - An `aria-live` region announces pick-ups, moves, results and opponent /
 *     new-position changes.
 *
 * Mouse users keep the unchanged react-chessboard drag-and-drop: the grid
 * overlay is `pointer-events-none`, so clicks fall through to the board beneath.
 */

const COLORS: Record<string, string> = { w: 'white', b: 'black' };
const PIECES: Record<string, string> = {
    K: 'king', Q: 'queen', R: 'rook', B: 'bishop', N: 'knight', P: 'pawn',
};
const PROMOTION_CHOICES: { letter: string; label: string }[] = [
    { letter: 'q', label: 'Queen' },
    { letter: 'r', label: 'Rook' },
    { letter: 'b', label: 'Bishop' },
    { letter: 'n', label: 'Knight' },
];

const FILES = ['a', 'b', 'c', 'd', 'e', 'f', 'g', 'h'];
const RANKS = ['1', '2', '3', '4', '5', '6', '7', '8'];

/** A piece code like "wP" / "bR" (colour letter + upper-case type). */
type PieceCode = string;

function describePiece(code: PieceCode | null): string | null {
    if (!code || code.length < 2) return null;
    const color = COLORS[code[0].toLowerCase()];
    const piece = PIECES[code[1].toUpperCase()];
    return color && piece ? `${color} ${piece}` : null;
}

/** Parse the placement field of a FEN into a `square -> pieceCode` map. */
function parsePosition(position: unknown): Record<string, PieceCode> {
    const map: Record<string, PieceCode> = {};
    if (typeof position !== 'string' || position.length === 0) return map;
    const placement = position.split(' ')[0];
    const rows = placement.split('/');
    if (rows.length !== 8) return map;
    for (let r = 0; r < 8; r++) {
        const rank = 8 - r; // FEN lists rank 8 first
        let file = 0;
        for (const ch of rows[r]) {
            if (/\d/.test(ch)) {
                file += Number(ch);
            } else {
                const color = ch === ch.toUpperCase() ? 'w' : 'b';
                const square = `${FILES[file]}${rank}`;
                map[square] = `${color}${ch.toUpperCase()}`;
                file += 1;
            }
        }
    }
    return map;
}

/** Build the 8x8 grid of square ids in visual (top-left → bottom-right) order. */
function buildVisualBoard(orientation: 'white' | 'black'): string[][] {
    const files = orientation === 'white' ? FILES : [...FILES].reverse();
    const ranks = orientation === 'white' ? [...RANKS].reverse() : RANKS;
    return ranks.map((rank) => files.map((file) => `${file}${rank}`));
}

type KeyboardMove = { sourceSquare: string; targetSquare: string; promotion?: string };

type Props = ComponentProps<typeof Chessboard> & {
    /**
     * Preferred move handler for keyboard moves; may carry a `promotion`. When
     * provided, a promotion chooser is offered for pawns reaching the last rank.
     * Falls back to `options.onPieceDrop` (which cannot carry a promotion, so it
     * auto-queens, matching the existing drag behaviour).
     */
    onKeyboardMove?: (move: KeyboardMove) => boolean;
};

export function AccessibleChessboard({ onKeyboardMove, ...props }: Props) {
    const ref = useRef<HTMLDivElement>(null);
    const options = props.options;
    const orientation: 'white' | 'black' = options?.boardOrientation === 'black' ? 'black' : 'white';
    const position = options?.position;

    const pieces = useMemo(() => parsePosition(position), [position]);
    const visualBoard = useMemo(() => buildVisualBoard(orientation), [orientation]);

    const [focusedSquare, setFocusedSquare] = useState<string>(() => visualBoard[6]?.[4] ?? 'e2');
    const [selectedSquare, setSelectedSquare] = useState<string | null>(null);
    const [pendingPromotion, setPendingPromotion] = useState<KeyboardMove | null>(null);
    const [announcement, setAnnouncement] = useState('');

    const cellRefs = useRef<Map<string, HTMLButtonElement>>(new Map());
    const promotionRef = useRef<HTMLButtonElement>(null);

    // Keep the visual draggables out of the tab order so the aria-hidden board
    // wrapper has no focusable descendants (which would be an a11y violation).
    // dnd-kit re-renders pieces, so a MutationObserver re-applies the attribute.
    useEffect(() => {
        const root = ref.current;
        if (!root) return;
        const board = root.querySelector('[data-accessible-board-visual]');
        if (!board) return;
        const detab = () => {
            board
                .querySelectorAll<HTMLElement>('[role="button"][aria-roledescription="draggable"]')
                .forEach((btn) => {
                    if (btn.getAttribute('tabindex') !== '-1') btn.setAttribute('tabindex', '-1');
                });
        };
        detab();
        const observer = new MutationObserver(detab);
        observer.observe(board, { childList: true, subtree: true });
        return () => observer.disconnect();
    }, []);

    const focusCell = useCallback((square: string) => {
        cellRefs.current.get(square)?.focus();
    }, []);

    const performMove = useCallback(
        (move: KeyboardMove): boolean => {
            let accepted = false;
            if (onKeyboardMove) {
                accepted = onKeyboardMove(move);
            } else if (options?.onPieceDrop) {
                const code = pieces[move.sourceSquare] ?? '';
                accepted = options.onPieceDrop({
                    piece: { isSparePiece: false, position: move.sourceSquare, pieceType: code },
                    sourceSquare: move.sourceSquare,
                    targetSquare: move.targetSquare,
                });
            }
            const movedPiece = describePiece(pieces[move.sourceSquare] ?? null) ?? 'piece';
            if (accepted) {
                setAnnouncement(`Moved ${movedPiece} from ${move.sourceSquare} to ${move.targetSquare}.`);
            } else {
                setAnnouncement(`Move from ${move.sourceSquare} to ${move.targetSquare} is not allowed.`);
            }
            setSelectedSquare(null);
            setPendingPromotion(null);
            return accepted;
        },
        [onKeyboardMove, options, pieces],
    );

    const needsPromotion = useCallback(
        (source: string, target: string): boolean => {
            const code = pieces[source];
            if (!code || code[1] !== 'P') return false;
            const targetRank = target[1];
            return (code[0] === 'w' && targetRank === '8') || (code[0] === 'b' && targetRank === '1');
        },
        [pieces],
    );

    const attemptMove = useCallback(
        (source: string, target: string) => {
            // Promotion is only offered when the host can carry the choice; the
            // onPieceDrop fallback auto-queens (parity with drag-and-drop).
            if (onKeyboardMove && needsPromotion(source, target)) {
                setPendingPromotion({ sourceSquare: source, targetSquare: target });
                setAnnouncement('Choose a piece to promote to.');
                return;
            }
            performMove({ sourceSquare: source, targetSquare: target });
        },
        [onKeyboardMove, needsPromotion, performMove],
    );

    const activateSquare = useCallback(
        (square: string) => {
            if (!selectedSquare) {
                const code = pieces[square];
                if (!code) {
                    setAnnouncement(`${square} is empty. Nothing to pick up.`);
                    return;
                }
                setSelectedSquare(square);
                const desc = describePiece(code) ?? 'piece';
                setAnnouncement(`Picked up ${desc} on ${square}. Navigate to a destination and press Enter to move, or Escape to cancel.`);
                return;
            }
            if (square === selectedSquare) {
                setSelectedSquare(null);
                setAnnouncement(`Cancelled. ${describePiece(pieces[square] ?? null) ?? 'Piece'} stays on ${square}.`);
                return;
            }
            attemptMove(selectedSquare, square);
        },
        [selectedSquare, pieces, attemptMove],
    );

    const handleKeyDown = useCallback(
        (event: React.KeyboardEvent, row: number, col: number) => {
            switch (event.key) {
                case 'ArrowUp':
                case 'ArrowDown':
                case 'ArrowLeft':
                case 'ArrowRight':
                case 'Home':
                case 'End': {
                    event.preventDefault();
                    let nextRow = row;
                    let nextCol = col;
                    if (event.key === 'ArrowUp') nextRow = Math.max(0, row - 1);
                    else if (event.key === 'ArrowDown') nextRow = Math.min(7, row + 1);
                    else if (event.key === 'ArrowLeft') nextCol = Math.max(0, col - 1);
                    else if (event.key === 'ArrowRight') nextCol = Math.min(7, col + 1);
                    else if (event.key === 'Home') nextCol = 0;
                    else if (event.key === 'End') nextCol = 7;
                    const next = visualBoard[nextRow][nextCol];
                    setFocusedSquare(next);
                    focusCell(next);
                    break;
                }
                case 'Enter':
                case ' ':
                    event.preventDefault();
                    activateSquare(visualBoard[row][col]);
                    break;
                case 'Escape':
                    if (selectedSquare) {
                        event.preventDefault();
                        const sq = selectedSquare;
                        setSelectedSquare(null);
                        setAnnouncement(`Cancelled. ${describePiece(pieces[sq] ?? null) ?? 'Piece'} stays on ${sq}.`);
                    }
                    break;
                default:
                    break;
            }
        },
        [visualBoard, focusCell, activateSquare, selectedSquare, pieces],
    );

    // Move browser focus onto the promotion chooser when it opens.
    useEffect(() => {
        if (pendingPromotion) promotionRef.current?.focus();
    }, [pendingPromotion]);

    const cancelPromotion = useCallback(() => {
        const source = pendingPromotion?.sourceSquare;
        setPendingPromotion(null);
        setAnnouncement('Promotion cancelled.');
        if (source) focusCell(source);
    }, [pendingPromotion, focusCell]);

    return (
        <div ref={ref} className="relative h-full w-full">
            <div data-accessible-board-visual aria-hidden="true" className="h-full w-full">
                <Chessboard {...props} />
            </div>

            <div
                role="grid"
                aria-label="Chess board"
                aria-describedby="accessible-board-instructions"
                className="absolute inset-0 flex flex-col pointer-events-none"
            >
                {visualBoard.map((rowSquares, row) => (
                    <div key={row} role="row" className="flex flex-1">
                        {rowSquares.map((square, col) => {
                            const code = pieces[square] ?? null;
                            const desc = describePiece(code);
                            const label = desc ? `${square}, ${desc}` : `${square}, empty`;
                            const isSelected = selectedSquare === square;
                            const isFocusTarget = focusedSquare === square;
                            return (
                                <button
                                    key={square}
                                    type="button"
                                    role="gridcell"
                                    ref={(el) => {
                                        if (el) cellRefs.current.set(square, el);
                                        else cellRefs.current.delete(square);
                                    }}
                                    tabIndex={isFocusTarget ? 0 : -1}
                                    aria-label={label}
                                    aria-selected={isSelected}
                                    data-square={square}
                                    onFocus={() => setFocusedSquare(square)}
                                    onKeyDown={(e) => handleKeyDown(e, row, col)}
                                    className="flex-1 bg-transparent km-focus-visible"
                                    style={
                                        isSelected
                                            ? { boxShadow: 'inset 0 0 0 3px var(--border-primary)' }
                                            : undefined
                                    }
                                />
                            );
                        })}
                    </div>
                ))}
            </div>

            <span id="accessible-board-instructions" className="sr-only">
                Use the arrow keys to move between squares. Press Enter or Space to pick up a piece,
                then move to a destination square and press Enter or Space again to move it. Press
                Escape to cancel.
            </span>

            <span className="sr-only" role="status" aria-live="polite" aria-atomic="true">
                {announcement}
            </span>

            {pendingPromotion && (
                <div
                    role="group"
                    aria-label="Promote pawn to"
                    className="absolute inset-x-0 bottom-0 z-10 flex flex-wrap justify-center gap-2 bg-bg-primary/95 border-t border-primary/20 p-3 pointer-events-auto"
                    onKeyDown={(e) => {
                        if (e.key === 'Escape') {
                            e.preventDefault();
                            cancelPromotion();
                        }
                    }}
                >
                    {PROMOTION_CHOICES.map((choice, index) => (
                        <button
                            key={choice.letter}
                            ref={index === 0 ? promotionRef : undefined}
                            type="button"
                            onClick={() =>
                                performMove({
                                    sourceSquare: pendingPromotion.sourceSquare,
                                    targetSquare: pendingPromotion.targetSquare,
                                    promotion: choice.letter,
                                })
                            }
                            className="px-4 py-2 border border-primary/20 text-primary rounded-sm font-serif text-sm km-interactive km-focus-visible"
                        >
                            {choice.label}
                        </button>
                    ))}
                </div>
            )}
        </div>
    );
}
