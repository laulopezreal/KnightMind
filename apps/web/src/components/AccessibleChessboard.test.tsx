import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent, within, act } from '@testing-library/react';
import { AccessibleChessboard } from './AccessibleChessboard';

// The real react-chessboard pulls in @dnd-kit and browser measurement APIs that
// jsdom lacks; the accessible grid overlay is what we're testing and it derives
// entirely from `options.position`, so stub the visual board.
vi.mock('react-chessboard', () => ({
    Chessboard: () => <div data-testid="visual-board" />,
}));

const START = 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1';

describe('AccessibleChessboard keyboard interaction', () => {
    it('exposes a labelled ARIA grid of 64 gridcells (regression: none existed before)', () => {
        render(<AccessibleChessboard options={{ position: START }} />);
        expect(screen.getByRole('grid')).toBeInTheDocument();
        expect(screen.getAllByRole('gridcell')).toHaveLength(64);
        // Squares are labelled with their occupant, not just their coordinate.
        expect(screen.getByRole('gridcell', { name: 'g1, white knight' })).toBeInTheDocument();
        expect(screen.getByRole('gridcell', { name: 'e4, empty' })).toBeInTheDocument();
    });

    it('lets a keyboard user pick up a piece and place it on a destination square', () => {
        const onKeyboardMove = vi.fn().mockReturnValue(true);
        render(<AccessibleChessboard onKeyboardMove={onKeyboardMove} options={{ position: START }} />);

        const from = screen.getByRole('gridcell', { name: 'g1, white knight' });
        act(() => from.focus());
        fireEvent.keyDown(from, { key: 'Enter' });
        // Announced pick-up.
        expect(screen.getByRole('status')).toHaveTextContent(/Picked up white knight on g1/i);

        const to = screen.getByRole('gridcell', { name: 'f3, empty' });
        act(() => to.focus());
        fireEvent.keyDown(to, { key: 'Enter' });

        expect(onKeyboardMove).toHaveBeenCalledWith({ sourceSquare: 'g1', targetSquare: 'f3' });
        expect(screen.getByRole('status')).toHaveTextContent(/Moved white knight from g1 to f3/i);
    });

    it('moves focus with arrow keys via roving tabindex', () => {
        render(<AccessibleChessboard options={{ position: START }} />);
        const e2 = screen.getByRole('gridcell', { name: 'e2, white pawn' });
        act(() => e2.focus());
        // ArrowUp (white orientation) goes toward rank 8: e2 -> e3.
        fireEvent.keyDown(e2, { key: 'ArrowUp' });
        expect(screen.getByRole('gridcell', { name: 'e3, empty' })).toHaveFocus();
    });

    it('cancels a pick-up with Escape', () => {
        render(<AccessibleChessboard options={{ position: START }} />);
        const from = screen.getByRole('gridcell', { name: 'b1, white knight' });
        act(() => from.focus());
        fireEvent.keyDown(from, { key: 'Enter' });
        expect(from).toHaveAttribute('aria-selected', 'true');
        fireEvent.keyDown(from, { key: 'Escape' });
        expect(from).toHaveAttribute('aria-selected', 'false');
        expect(screen.getByRole('status')).toHaveTextContent(/Cancelled/i);
    });

    it('offers an accessible promotion chooser for a pawn reaching the last rank', () => {
        const onKeyboardMove = vi.fn().mockReturnValue(true);
        // White pawn on a7.
        render(
            <AccessibleChessboard
                onKeyboardMove={onKeyboardMove}
                options={{ position: '8/P7/8/8/8/8/8/8 w - - 0 1' }}
            />,
        );

        const a7 = screen.getByRole('gridcell', { name: 'a7, white pawn' });
        act(() => a7.focus());
        fireEvent.keyDown(a7, { key: 'Enter' });
        const a8 = screen.getByRole('gridcell', { name: 'a8, empty' });
        act(() => a8.focus());
        fireEvent.keyDown(a8, { key: 'Enter' });

        // No move yet — a promotion choice is required first.
        expect(onKeyboardMove).not.toHaveBeenCalled();
        const chooser = screen.getByRole('dialog', { name: /promote pawn to/i });
        expect(chooser).toHaveAttribute('aria-modal', 'true');
        fireEvent.click(within(chooser).getByRole('button', { name: 'Knight' }));

        expect(onKeyboardMove).toHaveBeenCalledWith({
            sourceSquare: 'a7',
            targetSquare: 'a8',
            promotion: 'n',
        });
    });

    it('traps focus within the promotion chooser (Tab cycles, does not escape)', () => {
        const onKeyboardMove = vi.fn().mockReturnValue(true);
        render(
            <AccessibleChessboard
                onKeyboardMove={onKeyboardMove}
                options={{ position: '8/P7/8/8/8/8/8/8 w - - 0 1' }}
            />,
        );

        const a7 = screen.getByRole('gridcell', { name: 'a7, white pawn' });
        act(() => a7.focus());
        fireEvent.keyDown(a7, { key: 'Enter' });
        const a8 = screen.getByRole('gridcell', { name: 'a8, empty' });
        act(() => a8.focus());
        fireEvent.keyDown(a8, { key: 'Enter' });

        const chooser = screen.getByRole('dialog', { name: /promote pawn to/i });
        const first = within(chooser).getByRole('button', { name: 'Queen' });
        const last = within(chooser).getByRole('button', { name: 'Knight' });

        // Focus is moved into the chooser (first option) when it opens.
        expect(first).toHaveFocus();

        // Shift+Tab from the first option wraps to the last — focus stays inside.
        fireEvent.keyDown(first, { key: 'Tab', shiftKey: true });
        expect(last).toHaveFocus();

        // Tab from the last option wraps back to the first — never escapes.
        fireEvent.keyDown(last, { key: 'Tab' });
        expect(first).toHaveFocus();
    });

    it('falls back to onPieceDrop (auto-queen) when no onKeyboardMove is provided', () => {
        const onPieceDrop = vi.fn().mockReturnValue(true);
        render(
            <AccessibleChessboard
                options={{ position: '8/P7/8/8/8/8/8/8 w - - 0 1', onPieceDrop }}
            />,
        );
        const a7 = screen.getByRole('gridcell', { name: 'a7, white pawn' });
        act(() => a7.focus());
        fireEvent.keyDown(a7, { key: 'Enter' });
        const a8 = screen.getByRole('gridcell', { name: 'a8, empty' });
        act(() => a8.focus());
        fireEvent.keyDown(a8, { key: 'Enter' });
        // No chooser (fallback can't carry a promotion); move goes straight through.
        expect(screen.queryByRole('dialog', { name: /promote/i })).not.toBeInTheDocument();
        expect(onPieceDrop).toHaveBeenCalledWith(
            expect.objectContaining({ sourceSquare: 'a7', targetSquare: 'a8' }),
        );
    });
});
