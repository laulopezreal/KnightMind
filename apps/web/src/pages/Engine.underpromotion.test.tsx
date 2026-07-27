import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, act, within } from '@testing-library/react';
import Engine from './Engine';

// Engine wires the AccessibleChessboard's `onKeyboardMove` (dim 24) so keyboard
// users can underpromote in free-play instead of being auto-queened. We render
// the REAL AccessibleChessboard over a stubbed react-chessboard and assert the
// chosen promotion piece reaches the chess.js move.

vi.mock('../api', () => ({
    evaluateFen: vi.fn().mockResolvedValue({ best_move_uci: 'e2e4', eval: 0.5 }),
    getEngineStatus: vi.fn().mockResolvedValue({ available: false, message: 'offline' }),
    ApiError: class extends Error { detail?: string },
}));

vi.mock('react-router-dom', () => ({
    Link: ({ children, to }: { children: React.ReactNode; to: string }) => <a href={to}>{children}</a>,
}));

vi.mock('../context/ChessUsernameContext', () => ({
    useChessUsername: () => ({ username: 'testplayer', setEditorOpen: vi.fn() }),
}));

vi.mock('../api/puzzles', () => ({
    createManualPuzzle: vi.fn(),
}));

// Stub the visual board; the accessible grid overlay derives from options.position.
vi.mock('react-chessboard', () => ({
    Chessboard: () => <div data-testid="visual-board" />,
}));

const mockMove = vi.fn((move: unknown) => {
    void move; // captured for assertions via toHaveBeenCalledWith
    return { from: 'a7', to: 'a8', promotion: 'n', san: 'a8=N' };
});

vi.mock('chess.js', () => {
    class MockChess {
        private currentFen: string;
        constructor(fen?: string) {
            if (fen !== undefined && !fen.includes('/')) throw new Error('Invalid FEN');
            this.currentFen = fen || 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1';
        }
        fen() { return this.currentFen; }
        move(move?: unknown) { return mockMove(move); }
        get() { return null; }
        board() { return []; }
    }
    return { Chess: MockChess };
});

const STARTING_FEN = 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1';
// White pawn on a7, one step from promoting.
const PROMO_FEN = '8/P7/8/8/8/8/8/8 w - - 0 1';

describe('Engine keyboard underpromotion', () => {
    beforeEach(() => {
        vi.clearAllMocks();
    });

    it('lets a keyboard user choose the promotion piece (underpromote to knight)', async () => {
        render(<Engine />);

        // Load the promotion position so the accessible grid has a pawn on a7.
        const fenInput = screen.getByDisplayValue(STARTING_FEN);
        fireEvent.change(fenInput, { target: { value: PROMO_FEN } });
        fireEvent.click(screen.getByText('Load'));

        // Pick up the a7 pawn, then place it on a8.
        const a7 = await screen.findByRole('gridcell', { name: 'a7, white pawn' });
        act(() => a7.focus());
        fireEvent.keyDown(a7, { key: 'Enter' });
        const a8 = screen.getByRole('gridcell', { name: 'a8, empty' });
        act(() => a8.focus());
        fireEvent.keyDown(a8, { key: 'Enter' });

        // A promotion chooser appears (rather than auto-queening) and the chosen
        // piece flows through to the chess.js move.
        const chooser = screen.getByRole('dialog', { name: /promote pawn to/i });
        fireEvent.click(within(chooser).getByRole('button', { name: 'Knight' }));

        expect(mockMove).toHaveBeenCalledWith(
            expect.objectContaining({ from: 'a7', to: 'a8', promotion: 'n' }),
        );
    });
});
