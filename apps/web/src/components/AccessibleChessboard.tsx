import { useEffect, useRef, type ComponentProps } from 'react';
import { Chessboard } from 'react-chessboard';

/**
 * react-chessboard (v5, via @dnd-kit) renders each piece as a draggable
 * `role="button"` with NO accessible name — axe flags every one as
 * `aria-command-name` (32 on a full board). The library exposes no prop to
 * name them, so we label them ourselves from the DOM: each draggable wraps an
 * element carrying `data-piece` ("bR") and sits on a `data-square` ("a8"),
 * which we turn into "black rook on a8".
 *
 * dnd-kit re-renders the pieces on every drag and position change, so a
 * one-shot pass isn't enough — a MutationObserver scoped to this board keeps
 * the labels applied. Setting aria-label doesn't retrigger the observer (it
 * watches childList/subtree, not attributes), so there's no feedback loop.
 */

const COLORS: Record<string, string> = { w: 'white', b: 'black' };
const PIECES: Record<string, string> = {
    K: 'king', Q: 'queen', R: 'rook', B: 'bishop', N: 'knight', P: 'pawn',
};

function describePiece(code: string | null): string | null {
    if (!code || code.length < 2) return null;
    const color = COLORS[code[0].toLowerCase()];
    const piece = PIECES[code[1].toUpperCase()];
    return color && piece ? `${color} ${piece}` : null;
}

export function AccessibleChessboard(props: ComponentProps<typeof Chessboard>) {
    const ref = useRef<HTMLDivElement>(null);

    useEffect(() => {
        const root = ref.current;
        if (!root) return;

        const labelDraggables = () => {
            const draggables = root.querySelectorAll<HTMLElement>(
                '[role="button"][aria-roledescription="draggable"]',
            );
            draggables.forEach((btn) => {
                const pieceEl = btn.querySelector<HTMLElement>('[data-piece]');
                const code = pieceEl?.getAttribute('data-piece') ?? null;
                // Square comes from data-square on/above the button, or the piece
                // element id ("chessboard-piece-bR-a8").
                const square =
                    btn.getAttribute('data-square') ||
                    btn.closest('[data-square]')?.getAttribute('data-square') ||
                    pieceEl?.id.split('-').pop() ||
                    '';
                const piece = describePiece(code);
                const name = piece
                    ? `${piece}${square ? ` on ${square}` : ''}`
                    : square
                        ? `piece on ${square}`
                        : 'chess piece';
                if (btn.getAttribute('aria-label') !== name) {
                    btn.setAttribute('aria-label', name);
                }
            });
        };

        labelDraggables();
        const observer = new MutationObserver(labelDraggables);
        observer.observe(root, { childList: true, subtree: true });
        return () => observer.disconnect();
    }, []);

    return (
        <div ref={ref} className="h-full w-full">
            <Chessboard {...props} />
        </div>
    );
}
