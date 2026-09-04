import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import LibraryPuzzle from './LibraryPuzzle';

let mockUsername = 'testplayer';
let mockPuzzleId = 'puzzle-abc';

let mockSearchParams = new URLSearchParams();
const mockNavigate = vi.fn();
vi.mock('react-router-dom', () => ({
    useParams: () => ({ puzzleId: mockPuzzleId }),
    // ConnectAccountEmpty (the no-username branch) calls this. Its absence is
    // why that branch could not be tested at all.
    useNavigate: () => mockNavigate,
    useSearchParams: () => [mockSearchParams, vi.fn()],
    Link: ({ children, to, ...props }: { children: React.ReactNode; to: string; [key: string]: unknown }) => (
        <a href={to} {...props}>{children}</a>
    ),
}));

vi.mock('../context/ChessUsernameContext', () => ({
    useChessUsername: () => ({ username: mockUsername }),
}));

const {
    MockApiError,
    mockGetLibraryPuzzle,
    mockCheckPuzzle,
    mockRevealPuzzle,
    mockReviewPuzzle,
    mockGetPuzzleDiagnosis,
    mockGetSimilarPuzzles,
    mockChessMove,
} = vi.hoisted(() => {
    class MockApiError extends Error {
        statusCode: number;
        detail?: string;
        constructor(message: string, statusCode: number, detail?: string) {
            super(message);
            this.name = 'ApiError';
            this.statusCode = statusCode;
            this.detail = detail;
        }
    }
    return {
        MockApiError,
        mockGetLibraryPuzzle: vi.fn(),
        mockCheckPuzzle: vi.fn(),
        mockRevealPuzzle: vi.fn(),
        mockReviewPuzzle: vi.fn(),
        mockGetPuzzleDiagnosis: vi.fn(),
        mockGetSimilarPuzzles: vi.fn(),
        mockChessMove: vi.fn(),
    };
});

vi.mock('../api/core', () => ({
    ApiError: MockApiError,
}));

vi.mock('../api/puzzles', () => ({
    getLibraryPuzzle: (...args: unknown[]) => mockGetLibraryPuzzle(...args),
    checkPuzzle: (...args: unknown[]) => mockCheckPuzzle(...args),
    revealPuzzle: (...args: unknown[]) => mockRevealPuzzle(...args),
    reviewPuzzle: (...args: unknown[]) => mockReviewPuzzle(...args),
    getPuzzleDiagnosis: (...args: unknown[]) => mockGetPuzzleDiagnosis(...args),
    getSimilarPuzzles: (...args: unknown[]) => mockGetSimilarPuzzles(...args),
}));

vi.mock('react-chessboard', () => ({
    Chessboard: () => <div data-testid="chessboard">Chessboard</div>,
}));

vi.mock('chess.js', () => {
    class MockChess {
        load = vi.fn();
        move = vi.fn((move: { from: string; to: string; promotion?: string }) => {
            mockChessMove(move);
            return {
                from: move.from,
                to: move.to,
                promotion: move.to.endsWith('1') || move.to.endsWith('8') ? move.promotion : undefined,
            };
        });
        fen = vi.fn().mockReturnValue('rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1');
    }
    return { Chess: MockChess };
});

const MOCK_PUZZLE = {
    id: 'puzzle-abc',
    title: 'Deadly Fork', display_name: 'Deadly Fork',
    primary_motif: 'Fork',
    difficulty: 'medium' as const,
    swing: 3.0,
    fen: 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1',
    side_to_move: 'white',
    status: 'due' as const,
    attempts: 5,
    pass_count: 3,
    fail_count: 2,
    last_reviewed_at: '2026-01-15T12:00:00Z',
    last_result: 'pass',
    next_due_at: '2026-01-20T12:00:00Z',
    created_at: '2026-01-01T00:00:00Z',
    diagnosis_summary: null,
};

// The evidence names the solution — that is exactly why the card is gated.
const MOCK_DIAGNOSIS = {
    state: 'ready' as const,
    puzzle_id: 'puzzle-abc',
    primary_motif: 'fork',
    primary_cause: 'loose_piece_awareness',
    primary_cause_label: 'Loose piece awareness',
    secondary_causes: [],
    secondary_cause_labels: [],
    phase: 'middlegame',
    evidence: [{ id: 'best.move', label: 'Best move', value: 'e4 (forcing)' }],
    evidence_withheld: false,
    explanation: null,
    training_recommendation: null,
    user_confirmed_cause: null,
    source: 'rules',
    diagnosed_at: '2026-07-27T00:00:00Z',
};

describe('LibraryPuzzle', () => {
    beforeEach(() => {
        vi.resetAllMocks();
        mockUsername = 'testplayer';
        mockPuzzleId = 'puzzle-abc';
        mockSearchParams = new URLSearchParams();
        // Honour the requested id. The real endpoint always echoes back the
        // row it matched, and a fixed id here makes `puzzle?.id === puzzleId`
        // permanently false after navigation — which silently satisfies every
        // "did not fetch the sibling" assertion without exercising anything.
        mockGetLibraryPuzzle.mockImplementation(async (id: string) => ({
            ...MOCK_PUZZLE,
            id,
        }));
        mockGetPuzzleDiagnosis.mockResolvedValue(MOCK_DIAGNOSIS);
        // Default to no siblings: these tests are about the diagnosis surface,
        // and an unstubbed promise would fail them for the wrong reason.
        mockGetSimilarPuzzles.mockResolvedValue({ puzzles: [] });
        mockCheckPuzzle.mockResolvedValue({
            correct: true,
            result: 'pass',
            complete: true,
            reply: null,
            next_ply_index: null,
        });
        mockRevealPuzzle.mockResolvedValue({
            best_move_uci: 'e2e4',
            accept_moves_uci: ['e2e4'],
            solution_pv: ['e2e4'],
        });
        mockReviewPuzzle.mockResolvedValue({
            next_due_at: '2026-02-10T12:00:00Z',
            interval_days: 7,
            ease_factor: 2.1,
            feedback: 'Great job!',
            puzzle_info: { fen: MOCK_PUZZLE.fen, best_move: 'e2e4', side_to_move: 'white', swing: 3.0 },
            stats: { attempts: 6, pass_count: 4, fail_count: 2, last_reviewed_at: '2026-02-03', last_result: 'pass' },
        });
    });

    afterEach(() => {
        vi.unstubAllGlobals();
    });

    // --- Loading ---

    it('should show loading state', () => {
        mockGetLibraryPuzzle.mockReturnValue(new Promise(() => {}));
        render(<LibraryPuzzle />);
        expect(screen.getByText(/Loading puzzle/i)).toBeInTheDocument();
    });

    // --- Error states ---

    it('should show error when puzzle not found', async () => {
        mockGetLibraryPuzzle.mockRejectedValue(new MockApiError('Not found', 404));
        render(<LibraryPuzzle />);
        await waitFor(() => {
            expect(screen.getByText(/Puzzle not found/i)).toBeInTheDocument();
        });
    });

    it('should show error on API failure', async () => {
        mockGetLibraryPuzzle.mockRejectedValue(new Error('Server error'));
        render(<LibraryPuzzle />);
        await waitFor(() => {
            expect(screen.getByText(/Server error/i)).toBeInTheDocument();
        });
    });

    it('should show back to library link on error', async () => {
        mockGetLibraryPuzzle.mockRejectedValue(new MockApiError('Not found', 404));
        render(<LibraryPuzzle />);
        await waitFor(() => {
            expect(screen.getByText(/Back to Library/i)).toBeInTheDocument();
        });
    });

    it('offers Retry on a transient error', async () => {
        mockGetLibraryPuzzle.mockRejectedValue(new Error('Server error'));
        render(<LibraryPuzzle />);
        // A 500/network error is recoverable, so a Retry affordance appears
        // (consistent with the list/dashboard/openings error states).
        expect(await screen.findByRole('button', { name: /retry loading this puzzle/i })).toBeInTheDocument();
    });

    it('does not offer Retry on a 404 (refetch would miss again)', async () => {
        mockGetLibraryPuzzle.mockRejectedValue(new MockApiError('Not found', 404));
        render(<LibraryPuzzle />);
        expect(await screen.findByText(/Puzzle not found/i)).toBeInTheDocument();
        expect(screen.queryByRole('button', { name: /retry/i })).not.toBeInTheDocument();
    });

    // --- Successful load ---

    it('should display puzzle title', async () => {
        render(<LibraryPuzzle />);
        await waitFor(() => {
            expect(screen.getByText('Deadly Fork')).toBeInTheDocument();
        });
    });

    it('should display chessboard', async () => {
        render(<LibraryPuzzle />);
        await waitFor(() => {
            expect(screen.getByTestId('chessboard')).toBeInTheDocument();
        });
    });

    it('should display side to move', async () => {
        render(<LibraryPuzzle />);
        await waitFor(() => {
            expect(screen.getByText('White to Move')).toBeInTheDocument();
        });
    });

    it('should display metadata badges', async () => {
        render(<LibraryPuzzle />);
        await waitFor(() => {
            expect(screen.getByText('Medium')).toBeInTheDocument();
            expect(screen.getByText('Fork')).toBeInTheDocument();
        });
    });

    it('should display attempt stats', async () => {
        render(<LibraryPuzzle />);
        await waitFor(() => {
            expect(screen.getByText(/3\/5 solved/)).toBeInTheDocument();
            expect(screen.getByText(/2 failed/)).toBeInTheDocument();
        });
    });

    it('should show initial solving prompt', async () => {
        render(<LibraryPuzzle />);
        await waitFor(() => {
            expect(screen.getByText(/Find the best move/i)).toBeInTheDocument();
        });
    });

    it('keeps the guided board within its mobile track before actions without exposing the answer', async () => {
        render(<LibraryPuzzle />);
        await waitFor(() => expect(screen.getByText('Deadly Fork')).toBeInTheDocument());

        const instruction = screen.getByText(/drag a piece to its destination.*press enter to pick up.*enter to place/i);
        const guidance = screen.getByTestId('solve-guidance');
        const board = screen.getByTestId('solve-board');
        const boardFrame = screen.getByTestId('solve-board-frame');
        const actions = screen.getByTestId('solve-actions');
        const checkMove = screen.getByRole('button', { name: /check move/i });
        const history = screen.getByText(/3\/5 solved/);

        expect(instruction).toBeInTheDocument();
        expect(guidance).toContainElement(screen.getByText('White to Move'));
        expect(guidance).toContainElement(screen.getByText(/find the best move/i));
        expect(guidance.compareDocumentPosition(board) & Node.DOCUMENT_POSITION_PRECEDING).toBeTruthy();
        expect(board).toContainElement(boardFrame);
        expect(boardFrame).toContainElement(screen.getByTestId('chessboard'));
        expect(boardFrame).toHaveClass('w-full', 'max-w-[350px]', 'lg:max-w-[600px]', 'mx-auto');
        expect(board.compareDocumentPosition(actions) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
        expect(actions).toContainElement(checkMove);
        expect(checkMove.compareDocumentPosition(history) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
        expect(screen.queryByText('e2e4')).not.toBeInTheDocument();
        expect(mockRevealPuzzle).not.toHaveBeenCalled();
        expect(mockGetPuzzleDiagnosis).not.toHaveBeenCalled();
        expect(mockGetSimilarPuzzles).not.toHaveBeenCalled();
    });

    it('should show Check Move and Reveal buttons', async () => {
        render(<LibraryPuzzle />);
        await waitFor(() => {
            expect(screen.getByText('Check Move')).toBeInTheDocument();
            expect(screen.getByText('Reveal')).toBeInTheDocument();
        });
    });

    // --- Manual input ---

    it('should toggle UCI input', async () => {
        render(<LibraryPuzzle />);
        await waitFor(() => {
            expect(screen.getByText(/Type Move Manually/i)).toBeInTheDocument();
        });

        fireEvent.click(screen.getByText(/Type Move Manually/i));
        expect(screen.getByPlaceholderText('e.g. e2e4')).toBeInTheDocument();
    });

    // --- Reveal solution ---

    it('should show solution and record result when revealed', async () => {
        render(<LibraryPuzzle />);
        await waitFor(() => {
            expect(screen.getByText('Reveal')).toBeInTheDocument();
        });

        fireEvent.click(screen.getByText('Reveal'));

        // Should show solution
        await waitFor(() => {
            expect(screen.getByText('e2e4')).toBeInTheDocument();
        });

        // The first available Reveal must record one numeric, nonnegative
        // time_spent_ms value, rather than losing the timer to effect ordering.
        await waitFor(() => {
            expect(mockReviewPuzzle).toHaveBeenCalledTimes(1);
        });
        const [puzzleId, username, result, timeSpentMs] = mockReviewPuzzle.mock.calls[0];
        expect(puzzleId).toBe('puzzle-abc');
        expect(username).toBe('testplayer');
        expect(result).toBe('fail');
        expect(typeof timeSpentMs).toBe('number');
        expect(Number.isFinite(timeSpentMs)).toBe(true);
        expect(timeSpentMs).toBeGreaterThanOrEqual(0);
        expect(mockRevealPuzzle).toHaveBeenCalledTimes(1);
        expect(mockRevealPuzzle).toHaveBeenCalledWith('puzzle-abc', 'testplayer');
    });

    it('does not resolve or record when the reveal request fails', async () => {
        mockRevealPuzzle.mockRejectedValue(new Error('Reveal unavailable'));
        render(<LibraryPuzzle />);
        await screen.findByText('Reveal');

        fireEvent.click(screen.getByText('Reveal'));

        expect(await screen.findByText(/couldn't load the solution/i)).toBeInTheDocument();
        expect(screen.getByText(/find the best move/i)).toBeInTheDocument();
        expect(screen.queryByText('e2e4')).not.toBeInTheDocument();
        expect(mockReviewPuzzle).not.toHaveBeenCalled();
        expect(mockGetPuzzleDiagnosis).not.toHaveBeenCalled();
        expect(mockGetSimilarPuzzles).not.toHaveBeenCalled();
    });

    it('deduplicates an in-flight reveal and records one failure', async () => {
        let release: (value: { best_move_uci: string; accept_moves_uci: string[] }) => void = () => {};
        mockRevealPuzzle.mockReturnValue(new Promise((resolve) => { release = resolve; }));
        render(<LibraryPuzzle />);
        const reveal = await screen.findByRole('button', { name: 'Reveal' });

        fireEvent.click(reveal);
        fireEvent.click(reveal);
        expect(mockRevealPuzzle).toHaveBeenCalledTimes(1);

        release({ best_move_uci: 'e2e4', accept_moves_uci: ['e2e4'] });
        await screen.findByText('e2e4');
        await waitFor(() => expect(mockReviewPuzzle).toHaveBeenCalledTimes(1));
        expect(mockReviewPuzzle).toHaveBeenCalledWith(
            'puzzle-abc',
            'testplayer',
            'fail',
            expect.any(Number),
        );
    });

    it('ignores a reveal response that belongs to the previous puzzle', async () => {
        let release: (value: { best_move_uci: string; accept_moves_uci: string[] }) => void = () => {};
        mockRevealPuzzle.mockReturnValueOnce(new Promise((resolve) => { release = resolve; }));
        const { rerender } = render(<LibraryPuzzle />);
        fireEvent.click(await screen.findByRole('button', { name: 'Reveal' }));

        mockPuzzleId = 'sibling-1';
        rerender(<LibraryPuzzle />);
        await waitFor(() => expect(mockGetLibraryPuzzle).toHaveBeenCalledWith('sibling-1', 'testplayer'));
        release({ best_move_uci: 'e2e4', accept_moves_uci: ['e2e4'] });

        await waitFor(() => expect(screen.getByRole('button', { name: 'Reveal' })).toBeInTheDocument());
        expect(screen.queryByText('e2e4')).not.toBeInTheDocument();
        expect(mockReviewPuzzle).not.toHaveBeenCalled();
        expect(mockGetPuzzleDiagnosis).not.toHaveBeenCalled();
    });

    // --- Server-authoritative move checking ---

    it('checks a manual move on the server and records a correct result', async () => {
        render(<LibraryPuzzle />);
        await screen.findByText(/Type Move Manually/i);
        fireEvent.click(screen.getByText(/Type Move Manually/i));
        fireEvent.change(screen.getByPlaceholderText('e.g. e2e4'), { target: { value: 'E2E4' } });
        fireEvent.click(screen.getByRole('button', { name: /check move/i }));

        await screen.findByText('Correct!');
        expect(mockCheckPuzzle).toHaveBeenCalledWith('puzzle-abc', 'testplayer', 'e2e4', 0);
        expect(mockReviewPuzzle).toHaveBeenCalledWith(
            'puzzle-abc',
            'testplayer',
            'pass',
            expect.any(Number),
        );
        expect(mockRevealPuzzle).not.toHaveBeenCalled();
    });

    it('checks a board move on the server without requesting the answer', async () => {
        render(<LibraryPuzzle />);
        await screen.findByTestId('chessboard');

        fireEvent.keyDown(screen.getByRole('gridcell', { name: /e2, white pawn/i }), { key: 'Enter' });
        fireEvent.keyDown(screen.getByRole('gridcell', { name: /e4, empty/i }), { key: 'Enter' });

        await screen.findByText('Correct!');
        expect(mockCheckPuzzle).toHaveBeenCalledWith('puzzle-abc', 'testplayer', 'e2e4', 0);
        expect(mockRevealPuzzle).not.toHaveBeenCalled();
    });

    it('continues a multi-ply manual line and records only after the final solver move', async () => {
        mockCheckPuzzle
            .mockResolvedValueOnce({
                correct: true,
                result: 'pass',
                complete: false,
                reply: 'e7e5',
                next_ply_index: 2,
            })
            .mockResolvedValueOnce({ correct: true, result: 'pass', complete: true });
        render(<LibraryPuzzle />);
        fireEvent.click(await screen.findByText(/Type Move Manually/i));
        const input = screen.getByPlaceholderText('e.g. e2e4');

        fireEvent.change(input, { target: { value: 'e2e4' } });
        fireEvent.click(screen.getByRole('button', { name: /check move/i }));

        await waitFor(() => expect(mockCheckPuzzle).toHaveBeenCalledWith(
            'puzzle-abc', 'testplayer', 'e2e4', 0,
        ));
        await waitFor(() => expect(mockChessMove).toHaveBeenCalledWith({
            from: 'e7', to: 'e5', promotion: undefined,
        }));
        expect(screen.getByText(/find the best move/i)).toBeInTheDocument();
        expect(mockReviewPuzzle).not.toHaveBeenCalled();

        fireEvent.change(input, { target: { value: 'g1f3' } });
        fireEvent.click(screen.getByRole('button', { name: /check move/i }));

        await screen.findByText('Correct!');
        expect(mockCheckPuzzle).toHaveBeenLastCalledWith(
            'puzzle-abc', 'testplayer', 'g1f3', 2,
        );
        await waitFor(() => expect(mockReviewPuzzle).toHaveBeenCalledTimes(1));
    });

    it('continues a multi-ply board line without scoring the partial response', async () => {
        mockCheckPuzzle
            .mockResolvedValueOnce({
                correct: true,
                result: 'pass',
                complete: false,
                reply: 'e7e5',
                next_ply_index: 2,
            })
            .mockResolvedValueOnce({ correct: true, result: 'pass', complete: true });
        render(<LibraryPuzzle />);
        await screen.findByTestId('chessboard');

        fireEvent.keyDown(screen.getByRole('gridcell', { name: /e2, white pawn/i }), { key: 'Enter' });
        fireEvent.keyDown(screen.getByRole('gridcell', { name: /e4, empty/i }), { key: 'Enter' });

        await waitFor(() => expect(mockChessMove).toHaveBeenCalledWith({
            from: 'e7', to: 'e5', promotion: undefined,
        }));
        expect(mockReviewPuzzle).not.toHaveBeenCalled();

        fireEvent.keyDown(screen.getByRole('gridcell', { name: /g1, white knight/i }), { key: 'Enter' });
        fireEvent.keyDown(screen.getByRole('gridcell', { name: /f3, empty/i }), { key: 'Enter' });

        await screen.findByText('Correct!');
        expect(mockCheckPuzzle).toHaveBeenLastCalledWith(
            'puzzle-abc', 'testplayer', 'g1f3', 2,
        );
        await waitFor(() => expect(mockReviewPuzzle).toHaveBeenCalledTimes(1));
    });

    it('ignores a stale partial check after the same puzzle reloads for another user', async () => {
        let release: (value: {
            correct: boolean;
            result: 'pass';
            complete: boolean;
            reply: string;
            next_ply_index: number;
        }) => void = () => {};
        mockCheckPuzzle.mockReturnValueOnce(new Promise((resolve) => { release = resolve; }));
        const { rerender } = render(<LibraryPuzzle />);
        fireEvent.click(await screen.findByText(/Type Move Manually/i));
        fireEvent.change(screen.getByPlaceholderText('e.g. e2e4'), { target: { value: 'e2e4' } });
        fireEvent.click(screen.getByRole('button', { name: /check move/i }));

        mockUsername = 'otherplayer';
        rerender(<LibraryPuzzle />);
        await waitFor(() => expect(mockGetLibraryPuzzle).toHaveBeenCalledWith(
            'puzzle-abc', 'otherplayer',
        ));
        release({
            correct: true,
            result: 'pass',
            complete: false,
            reply: 'e7e5',
            next_ply_index: 2,
        });

        await waitFor(() => expect(screen.getByRole('button', { name: 'Reveal' })).toBeInTheDocument());
        expect(mockChessMove).not.toHaveBeenCalledWith({
            from: 'e7', to: 'e5', promotion: undefined,
        });
        expect(mockReviewPuzzle).not.toHaveBeenCalled();
    });

    it('ignores a check response after the puzzle page unmounts', async () => {
        let release: (value: { correct: boolean; result: 'pass'; complete: boolean }) => void = () => {};
        mockCheckPuzzle.mockReturnValueOnce(new Promise((resolve) => { release = resolve; }));
        const { unmount } = render(<LibraryPuzzle />);
        fireEvent.click(await screen.findByText(/Type Move Manually/i));
        fireEvent.change(screen.getByPlaceholderText('e.g. e2e4'), { target: { value: 'e2e4' } });
        fireEvent.click(screen.getByRole('button', { name: /check move/i }));
        await waitFor(() => expect(mockCheckPuzzle).toHaveBeenCalledTimes(1));

        unmount();
        release({ correct: true, result: 'pass', complete: true });

        await Promise.resolve();
        expect(mockReviewPuzzle).not.toHaveBeenCalled();
    });

    it('deduplicates an in-flight final check and records one pass', async () => {
        let release: (value: { correct: boolean; result: 'pass'; complete: boolean }) => void = () => {};
        mockCheckPuzzle.mockReturnValueOnce(new Promise((resolve) => { release = resolve; }));
        render(<LibraryPuzzle />);
        fireEvent.click(await screen.findByText(/Type Move Manually/i));
        fireEvent.change(screen.getByPlaceholderText('e.g. e2e4'), { target: { value: 'e2e4' } });
        const check = screen.getByRole('button', { name: /check move/i });

        fireEvent.click(check);
        fireEvent.click(check);
        expect(mockCheckPuzzle).toHaveBeenCalledTimes(1);
        release({ correct: true, result: 'pass', complete: true });

        await screen.findByText('Correct!');
        await waitFor(() => expect(mockReviewPuzzle).toHaveBeenCalledTimes(1));
    });

    it('keeps the current solver ply after a wrong move and resets it on retry', async () => {
        mockCheckPuzzle
            .mockResolvedValueOnce({
                correct: true,
                result: 'pass',
                complete: false,
                reply: 'e7e5',
                next_ply_index: 2,
            })
            .mockResolvedValueOnce({ correct: false, result: 'fail', complete: false })
            .mockResolvedValueOnce({ correct: true, result: 'pass', complete: true });
        render(<LibraryPuzzle />);
        fireEvent.click(await screen.findByText(/Type Move Manually/i));
        const input = screen.getByPlaceholderText('e.g. e2e4');

        fireEvent.change(input, { target: { value: 'e2e4' } });
        fireEvent.click(screen.getByRole('button', { name: /check move/i }));
        await waitFor(() => expect(mockCheckPuzzle).toHaveBeenCalledTimes(1));
        fireEvent.change(input, { target: { value: 'a2a3' } });
        fireEvent.click(screen.getByRole('button', { name: /check move/i }));

        await screen.findByText('Incorrect.');
        expect(mockCheckPuzzle).toHaveBeenLastCalledWith(
            'puzzle-abc', 'testplayer', 'a2a3', 2,
        );
        expect(mockReviewPuzzle).not.toHaveBeenCalled();

        fireEvent.click(screen.getByRole('button', { name: 'Try Again' }));
        fireEvent.change(input, { target: { value: 'd2d4' } });
        fireEvent.click(screen.getByRole('button', { name: /check move/i }));

        await screen.findByText('Correct!');
        expect(mockCheckPuzzle).toHaveBeenLastCalledWith(
            'puzzle-abc', 'testplayer', 'd2d4', 0,
        );
    });

    it('reveals during a partial line and records exactly one failure', async () => {
        mockCheckPuzzle.mockResolvedValueOnce({
            correct: true,
            result: 'pass',
            complete: false,
            reply: 'e7e5',
            next_ply_index: 2,
        });
        render(<LibraryPuzzle />);
        fireEvent.click(await screen.findByText(/Type Move Manually/i));
        fireEvent.change(screen.getByPlaceholderText('e.g. e2e4'), { target: { value: 'e2e4' } });
        fireEvent.click(screen.getByRole('button', { name: /check move/i }));
        await waitFor(() => expect(mockCheckPuzzle).toHaveBeenCalledTimes(1));

        fireEvent.click(screen.getByRole('button', { name: 'Reveal' }));

        await screen.findByText('e2e4');
        await waitFor(() => expect(mockReviewPuzzle).toHaveBeenCalledTimes(1));
        expect(mockReviewPuzzle).toHaveBeenCalledWith(
            'puzzle-abc', 'testplayer', 'fail', expect.any(Number),
        );
    });

    it('keeps a wrong response answerless and does not record a pass', async () => {
        mockCheckPuzzle.mockResolvedValue({ correct: false, result: 'fail' });
        render(<LibraryPuzzle />);
        await screen.findByText(/Type Move Manually/i);
        fireEvent.click(screen.getByText(/Type Move Manually/i));
        fireEvent.change(screen.getByPlaceholderText('e.g. e2e4'), { target: { value: 'd2d3' } });
        fireEvent.click(screen.getByRole('button', { name: /check move/i }));

        await screen.findByText('Incorrect.');
        expect(mockCheckPuzzle).toHaveBeenCalledWith('puzzle-abc', 'testplayer', 'd2d3', 0);
        expect(mockRevealPuzzle).not.toHaveBeenCalled();
        expect(mockReviewPuzzle).not.toHaveBeenCalled();
        expect(screen.queryByText('e2e4')).not.toBeInTheDocument();
    });

    it('keeps post-mortem content locked after a wrong check and allows a clean retry', async () => {
        mockCheckPuzzle
            .mockResolvedValueOnce({ correct: false, result: 'fail', complete: false })
            .mockResolvedValueOnce({ correct: true, result: 'pass', complete: true });
        render(<LibraryPuzzle />);
        fireEvent.click(await screen.findByText(/Type Move Manually/i));
        const input = screen.getByPlaceholderText('e.g. e2e4');

        fireEvent.change(input, { target: { value: 'd2d3' } });
        fireEvent.click(screen.getByRole('button', { name: /check move/i }));

        await screen.findByText('Incorrect.');
        expect(mockCheckPuzzle).toHaveBeenLastCalledWith(
            'puzzle-abc', 'testplayer', 'd2d3', 0,
        );
        expect(mockGetPuzzleDiagnosis).not.toHaveBeenCalled();
        expect(mockGetSimilarPuzzles).not.toHaveBeenCalled();
        expect(mockRevealPuzzle).not.toHaveBeenCalled();
        expect(mockReviewPuzzle).not.toHaveBeenCalled();
        expect(screen.queryByRole('region', { name: /mistake diagnosis/i })).not.toBeInTheDocument();
        expect(screen.queryByText('Loose piece awareness')).not.toBeInTheDocument();
        expect(screen.queryByText('e4 (forcing)')).not.toBeInTheDocument();
        expect(screen.queryByText('e2e4')).not.toBeInTheDocument();

        fireEvent.click(screen.getByRole('button', { name: 'Try Again' }));
        fireEvent.change(input, { target: { value: 'e2e4' } });
        fireEvent.click(screen.getByRole('button', { name: /check move/i }));

        await screen.findByText('Correct!');
        expect(mockCheckPuzzle).toHaveBeenLastCalledWith(
            'puzzle-abc', 'testplayer', 'e2e4', 0,
        );
    });

    it('keeps the puzzle unsolved when a move check fails', async () => {
        mockCheckPuzzle.mockRejectedValue(new Error('Check unavailable'));
        render(<LibraryPuzzle />);
        await screen.findByText(/Type Move Manually/i);
        fireEvent.click(screen.getByText(/Type Move Manually/i));
        fireEvent.change(screen.getByPlaceholderText('e.g. e2e4'), { target: { value: 'e2e4' } });
        fireEvent.click(screen.getByRole('button', { name: /check move/i }));

        expect(await screen.findByText(/couldn't check that move/i)).toBeInTheDocument();
        expect(screen.getByText(/find the best move/i)).toBeInTheDocument();
        expect(mockReviewPuzzle).not.toHaveBeenCalled();
        expect(mockGetPuzzleDiagnosis).not.toHaveBeenCalled();
    });

    it('retries the same solver ply after a later move check fails', async () => {
        mockCheckPuzzle
            .mockResolvedValueOnce({
                correct: true,
                result: 'pass',
                complete: false,
                reply: 'e7e5',
                next_ply_index: 2,
            })
            .mockRejectedValueOnce(new Error('Check unavailable'))
            .mockResolvedValueOnce({ correct: true, result: 'pass', complete: true });
        render(<LibraryPuzzle />);
        fireEvent.click(await screen.findByText(/Type Move Manually/i));
        const input = screen.getByPlaceholderText('e.g. e2e4');

        fireEvent.change(input, { target: { value: 'e2e4' } });
        fireEvent.click(screen.getByRole('button', { name: /check move/i }));
        await waitFor(() => expect(mockCheckPuzzle).toHaveBeenCalledTimes(1));
        fireEvent.change(input, { target: { value: 'g1f3' } });
        fireEvent.click(screen.getByRole('button', { name: /check move/i }));
        await screen.findByText(/couldn't check that move/i);

        fireEvent.change(input, { target: { value: 'g1f3' } });
        fireEvent.click(screen.getByRole('button', { name: /check move/i }));

        await screen.findByText('Correct!');
        expect(mockCheckPuzzle).toHaveBeenLastCalledWith(
            'puzzle-abc', 'testplayer', 'g1f3', 2,
        );
        await waitFor(() => expect(mockReviewPuzzle).toHaveBeenCalledTimes(1));
    });

    // --- Recorded confirmation ---

    it('should show Recorded confirmation after result is submitted', async () => {
        render(<LibraryPuzzle />);
        await waitFor(() => {
            expect(screen.getByText('Reveal')).toBeInTheDocument();
        });

        fireEvent.click(screen.getByText('Reveal'));

        await waitFor(() => {
            expect(screen.getByText('Recorded')).toBeInTheDocument();
        });
    });

    it('should show next review date after recording', async () => {
        render(<LibraryPuzzle />);
        await waitFor(() => {
            expect(screen.getByText('Reveal')).toBeInTheDocument();
        });

        fireEvent.click(screen.getByText('Reveal'));

        await waitFor(() => {
            expect(screen.getByText(/Next review:/i)).toBeInTheDocument();
        });
    });

    it('should show Back to Library button after completion', async () => {
        render(<LibraryPuzzle />);
        await waitFor(() => {
            expect(screen.getByText('Reveal')).toBeInTheDocument();
        });

        fireEvent.click(screen.getByText('Reveal'));

        await waitFor(() => {
            expect(screen.getByText('Back to Library')).toBeInTheDocument();
        });
    });

    // --- Back link ---

    it('should show back to library link', async () => {
        render(<LibraryPuzzle />);
        await waitFor(() => {
            const links = screen.getAllByText(/Back to Library/i);
            expect(links.length).toBeGreaterThan(0);
        });
    });

    // --- Record error ---

    it('should show error message when recording result fails', async () => {
        mockReviewPuzzle.mockRejectedValue(new Error('Network error'));
        render(<LibraryPuzzle />);
        await waitFor(() => {
            expect(screen.getByText('Reveal')).toBeInTheDocument();
        });

        fireEvent.click(screen.getByText('Reveal'));

        await waitFor(() => {
            expect(screen.getByText('Network error')).toBeInTheDocument();
        });
    });

    it('should show fallback error when recording fails with non-Error', async () => {
        mockReviewPuzzle.mockRejectedValue('unknown');
        render(<LibraryPuzzle />);
        await waitFor(() => {
            expect(screen.getByText('Reveal')).toBeInTheDocument();
        });

        fireEvent.click(screen.getByText('Reveal'));

        await waitFor(() => {
            expect(screen.getByText(/Failed to save your result/i)).toBeInTheDocument();
        });
    });

    // --- API call ---

    it('should fetch puzzle by ID', async () => {
        render(<LibraryPuzzle />);
        await waitFor(() => {
            expect(mockGetLibraryPuzzle).toHaveBeenCalledWith('puzzle-abc', 'testplayer');
        });
    });

    describe('mistake diagnosis', () => {
        it('is not shown — or even requested — while the puzzle is unsolved', async () => {
            // The diagnosis evidence names the solution move, so fetching it
            // mid-solve would put the answer in the network tab even if the UI
            // hid it.
            render(<LibraryPuzzle />);
            await waitFor(() => expect(screen.getByText('Deadly Fork')).toBeInTheDocument());

            expect(screen.queryByRole('region', { name: /mistake diagnosis/i })).not.toBeInTheDocument();
            expect(mockGetPuzzleDiagnosis).not.toHaveBeenCalled();
        });

        it('appears once the solution has been revealed', async () => {
            render(<LibraryPuzzle />);
            await waitFor(() => expect(screen.getByText('Deadly Fork')).toBeInTheDocument());

            fireEvent.click(screen.getByRole('button', { name: /reveal/i }));

            await waitFor(() =>
                expect(screen.getByRole('region', { name: /mistake diagnosis/i })).toBeInTheDocument()
            );
            expect(screen.getByText('Loose piece awareness')).toBeInTheDocument();
        });

        it('opts in to the evidence explicitly when it does fetch', async () => {
            render(<LibraryPuzzle />);
            await waitFor(() => expect(screen.getByText('Deadly Fork')).toBeInTheDocument());
            fireEvent.click(screen.getByRole('button', { name: /reveal/i }));

            await waitFor(() => expect(mockGetPuzzleDiagnosis).toHaveBeenCalled());
            expect(mockGetPuzzleDiagnosis).toHaveBeenCalledWith('puzzle-abc', 'testplayer', true);
        });

        it('stays silent when the diagnosis fetch fails', async () => {
            // Supplementary content: a solved puzzle must not turn into an
            // error page because an enrichment call failed.
            mockGetPuzzleDiagnosis.mockRejectedValue(new Error('boom'));
            render(<LibraryPuzzle />);
            await waitFor(() => expect(screen.getByText('Deadly Fork')).toBeInTheDocument());
            fireEvent.click(screen.getByRole('button', { name: /reveal/i }));

            await waitFor(() => expect(mockGetPuzzleDiagnosis).toHaveBeenCalled());
            expect(screen.queryByText(/boom/i)).not.toBeInTheDocument();
            expect(screen.getByText('Deadly Fork')).toBeInTheDocument();
        });
    });

    describe('navigating between puzzles', () => {
        it('does not carry a revealed state onto the next puzzle', async () => {
            // SimilarWeaknessCard is the first link from /library/:id TO
            // /library/:id, so the component is reused rather than remounted.
            // If solved/revealed state survives, the next puzzle opens with its
            // answer already granted — and its diagnosis, which names the best
            // move, is fetched with reveal=true without an attempt.
            const { rerender } = render(<LibraryPuzzle />);
            await waitFor(() => expect(screen.getByText('Deadly Fork')).toBeInTheDocument());
            fireEvent.click(screen.getByRole('button', { name: /reveal/i }));
            await waitFor(() => expect(mockGetPuzzleDiagnosis).toHaveBeenCalled());

            mockGetPuzzleDiagnosis.mockClear();
            mockPuzzleId = 'sibling-1';
            rerender(<LibraryPuzzle />);
            await waitFor(() => expect(mockGetLibraryPuzzle).toHaveBeenCalledWith('sibling-1', 'testplayer'));

            expect(mockGetPuzzleDiagnosis).not.toHaveBeenCalled();
            expect(screen.getByRole('button', { name: /reveal/i })).toBeInTheDocument();
        });

        it('loads the sibling as its own puzzle, not a rerun of the previous one', async () => {
            // The counterpart to the leak tests: having proved the sibling does
            // NOT inherit resolved state, prove it can still resolve on its own
            // and fetches its own diagnosis and siblings. Without this, every
            // navigation assertion is satisfied by the page simply never
            // working after a navigation.
            const { rerender } = render(<LibraryPuzzle />);
            await waitFor(() => expect(screen.getByText('Deadly Fork')).toBeInTheDocument());
            fireEvent.click(screen.getByRole('button', { name: /reveal/i }));
            await waitFor(() => expect(mockGetSimilarPuzzles).toHaveBeenCalledWith('puzzle-abc', 'testplayer'));

            mockGetPuzzleDiagnosis.mockClear();
            mockGetSimilarPuzzles.mockClear();
            mockPuzzleId = 'sibling-1';
            rerender(<LibraryPuzzle />);
            await waitFor(() => expect(mockGetLibraryPuzzle).toHaveBeenCalledWith('sibling-1', 'testplayer'));

            // Resolved state did not carry over...
            expect(mockGetPuzzleDiagnosis).not.toHaveBeenCalled();
            // ...and the sibling still works, fetching ITS own post-mortem.
            fireEvent.click(screen.getByRole('button', { name: /reveal/i }));
            await waitFor(() =>
                expect(mockGetPuzzleDiagnosis).toHaveBeenCalledWith('sibling-1', 'testplayer', true)
            );
            expect(mockGetSimilarPuzzles).toHaveBeenCalledWith('sibling-1', 'testplayer');
        });

        it('does not show the previous puzzle\'s siblings on the next one', async () => {
            // `similar` is reset on load; without that reset the similar-effect
            // early-returns on `|| similar` and the sibling page renders the
            // PREVIOUS puzzle's cluster and reason sentence as though it were
            // its own. Deleting setSimilar(null) previously failed no test.
            mockGetSimilarPuzzles.mockResolvedValue({
                cause: 'loose_piece_awareness',
                cause_label: 'Loose piece awareness',
                match: 'exact',
                reason: 'ANCHOR-A REASON',
                puzzles: [
                    {
                        id: 'from-puzzle-a',
                        title: 'Belongs to puzzle A', display_name: 'Belongs to puzzle A',
                        primary_motif: 'hanging_piece',
                        difficulty: 'medium',
                        swing: 3.1,
                        fen: '8/8/8/8/8/8/8/8 w - - 0 1',
                        side_to_move: 'white',
                        created_at: null,
                        attempts: 1,
                        fail_count: 1,
                    },
                ],
            });
            const { rerender } = render(<LibraryPuzzle />);
            await waitFor(() => expect(screen.getByText('Deadly Fork')).toBeInTheDocument());
            fireEvent.click(screen.getByRole('button', { name: /reveal/i }));
            await waitFor(() => expect(screen.getByText('ANCHOR-A REASON')).toBeInTheDocument());

            mockGetSimilarPuzzles.mockResolvedValue({ puzzles: [] });
            mockPuzzleId = 'sibling-1';
            rerender(<LibraryPuzzle />);
            await waitFor(() => expect(mockGetLibraryPuzzle).toHaveBeenCalledWith('sibling-1', 'testplayer'));
            fireEvent.click(screen.getByRole('button', { name: /reveal/i }));

            await waitFor(() => expect(mockGetSimilarPuzzles).toHaveBeenCalledWith('sibling-1', 'testplayer'));
            expect(screen.queryByText('ANCHOR-A REASON')).not.toBeInTheDocument();
            expect(screen.queryByText('Belongs to puzzle A')).not.toBeInTheDocument();
        });

        it('does not leak the next puzzle when the current diagnosis never loaded', async () => {
            // The reset runs AFTER the getLibraryPuzzle round-trip, so for the
            // whole request `status` is still 'revealed' while puzzleId is
            // already the sibling's. The test above only passes because its
            // diagnosis had loaded, making the effect's `|| diagnosis` guard
            // short-circuit. When that fetch failed — or simply lost the race
            // against /similar — nothing stops it.
            mockGetPuzzleDiagnosis.mockRejectedValue(new Error('diagnosis down'));
            const { rerender } = render(<LibraryPuzzle />);
            await waitFor(() => expect(screen.getByText('Deadly Fork')).toBeInTheDocument());
            fireEvent.click(screen.getByRole('button', { name: /reveal/i }));
            await waitFor(() => expect(mockGetPuzzleDiagnosis).toHaveBeenCalled());

            mockGetPuzzleDiagnosis.mockClear();
            mockPuzzleId = 'sibling-1';
            rerender(<LibraryPuzzle />);
            await waitFor(() => expect(mockGetLibraryPuzzle).toHaveBeenCalledWith('sibling-1', 'testplayer'));

            expect(mockGetPuzzleDiagnosis).not.toHaveBeenCalled();
        });
    });

    describe('similar weaknesses', () => {
        it('is not requested while the puzzle is unsolved', async () => {
            // Siblings name the shared motif, so asking for them mid-solve
            // would hand over the tactic before the attempt.
            render(<LibraryPuzzle />);
            await waitFor(() => expect(screen.getByText('Deadly Fork')).toBeInTheDocument());

            expect(screen.queryByRole('heading', { name: /more like this weakness/i })).not.toBeInTheDocument();
            expect(mockGetSimilarPuzzles).not.toHaveBeenCalled();
        });

        it('appears once the solution has been revealed', async () => {
            mockGetSimilarPuzzles.mockResolvedValue({
                cause: 'calculation_stopped_early',
                cause_label: 'calculation stopped early',
                match: 'exact',
                reason: 'Same mistake — calculation stopped early — on a Fork in the middlegame.',
                puzzles: [
                    {
                        id: 'sibling-1',
                        title: 'Another fork missed', display_name: 'Another fork missed',
                        primary_motif: 'Fork',
                        difficulty: 'medium',
                        swing: 3.1,
                        fen: '8/8/8/8/8/8/8/8 w - - 0 1',
                        side_to_move: 'white',
                        created_at: null,
                        attempts: 1,
                        fail_count: 1,
                    },
                ],
            });
            render(<LibraryPuzzle />);
            await waitFor(() => expect(screen.getByText('Deadly Fork')).toBeInTheDocument());
            fireEvent.click(screen.getByRole('button', { name: /reveal/i }));

            await waitFor(() =>
                expect(screen.getByRole('heading', { name: /more like this weakness/i })).toBeInTheDocument()
            );
            expect(screen.getByText('Another fork missed')).toBeInTheDocument();
        });

        it('stays silent when the siblings fetch fails', async () => {
            mockGetSimilarPuzzles.mockRejectedValue(new Error('kaboom'));
            render(<LibraryPuzzle />);
            await waitFor(() => expect(screen.getByText('Deadly Fork')).toBeInTheDocument());
            fireEvent.click(screen.getByRole('button', { name: /reveal/i }));

            await waitFor(() => expect(mockGetSimilarPuzzles).toHaveBeenCalled());
            expect(screen.queryByText(/kaboom/i)).not.toBeInTheDocument();
            expect(screen.getByText('Deadly Fork')).toBeInTheDocument();
        });
    });


    describe('while a sibling is loading', () => {
        it('does not leave the previous puzzle on screen', async () => {
            // useAsyncData keeps `data` during a refetch and reports it via
            // `refreshing`, not `loading` -- `loading` is FIRST load only. Wiring
            // the page to `loading` alone meant a sibling navigation showed the
            // previous puzzle's title, board and, if it had been revealed, its
            // SOLUTION, under the new puzzle's URL for the whole round trip.
            const { rerender } = render(<LibraryPuzzle />);
            await waitFor(() => expect(screen.getByText('Deadly Fork')).toBeInTheDocument());

            let release: (v: unknown) => void = () => {};
            mockGetLibraryPuzzle.mockImplementationOnce(
                () => new Promise((resolve) => { release = resolve; }),
            );
            mockPuzzleId = 'sibling-1';
            rerender(<LibraryPuzzle />);

            await waitFor(() =>
                expect(mockGetLibraryPuzzle).toHaveBeenCalledWith('sibling-1', 'testplayer'),
            );
            expect(screen.queryByText('Deadly Fork')).not.toBeInTheDocument();
            expect(screen.getByText(/loading puzzle/i)).toBeInTheDocument();

            release({
                ...MOCK_PUZZLE,
                id: 'sibling-1',
                title: 'Sibling',
                display_name: 'Sibling',
            });
            await waitFor(() => expect(screen.getByText('Sibling')).toBeInTheDocument());
        });

        it('resets solve state when the same puzzle is loaded again', async () => {
            // Keying the reset on `puzzle.id` skipped it whenever an id reloaded
            // -- a username change, or a retry that re-lands the id already in
            // `data`. A revealed solution then carried into the next load of
            // that same puzzle. One instance, rerendered: two render() calls
            // leave two mounted copies and make the query ambiguous.
            const { rerender } = render(<LibraryPuzzle />);
            await waitFor(() => expect(screen.getByText('Deadly Fork')).toBeInTheDocument());
            fireEvent.click(screen.getByRole('button', { name: /reveal/i }));
            await waitFor(() =>
                expect(screen.queryByRole('button', { name: /reveal/i })).not.toBeInTheDocument(),
            );

            // Same puzzle id, refetched because the account changed.
            mockUsername = 'otherplayer';
            rerender(<LibraryPuzzle />);
            await waitFor(() =>
                expect(mockGetLibraryPuzzle).toHaveBeenCalledWith('puzzle-abc', 'otherplayer'),
            );

            // Back to unsolved: the Reveal button returns.
            await waitFor(() =>
                expect(screen.getByRole('button', { name: /reveal/i })).toBeInTheDocument(),
            );
        });
    });

    it('asks for an account instead of hanging on a spinner', async () => {
        // This page had no no-username branch: its fetch returned early BEFORE
        // setting the loading flag, so `isLoading` kept its initial `true` and
        // the page showed "Loading puzzle..." forever with no account connected.
        mockUsername = '';
        render(<LibraryPuzzle />);

        expect(
            await screen.findByText('Connect your Chess.com account'),
        ).toBeInTheDocument();
        expect(screen.queryByText(/loading puzzle/i)).not.toBeInTheDocument();
        expect(mockGetLibraryPuzzle).not.toHaveBeenCalled();
    });

    describe('back-navigation context', () => {
        it('shows Back to Library and links to /library when no session origin', async () => {
            // Normal entry: no ?from=session param.
            mockSearchParams = new URLSearchParams();
            render(<LibraryPuzzle />);
            await waitFor(() => expect(screen.getByText('Deadly Fork')).toBeInTheDocument());

            const backLink = screen.getByRole('link', { name: /back to library/i });
            expect(backLink).toHaveAttribute('href', '/library');
        });

        it('shows Back to Session Summary and links to /puzzles when from=session', async () => {
            mockSearchParams = new URLSearchParams('from=session');
            render(<LibraryPuzzle />);
            await waitFor(() => expect(screen.getByText('Deadly Fork')).toBeInTheDocument());

            // Header back-link must say "Back to Session Summary" and point to /puzzles.
            const backLink = screen.getByRole('link', { name: /back to session summary/i });
            expect(backLink).toHaveAttribute('href', '/puzzles');
        });

        it('post-completion button targets /puzzles when from=session', async () => {
            mockSearchParams = new URLSearchParams('from=session');
            render(<LibraryPuzzle />);
            await waitFor(() => expect(screen.getByText('Reveal')).toBeInTheDocument());
            fireEvent.click(screen.getByText('Reveal'));

            // After completion both the header back-link AND the green CTA show.
            // The green CTA is the second match; both must point to /puzzles.
            await waitFor(() =>
                expect(screen.getAllByRole('link', { name: /back to session summary/i }).length).toBeGreaterThanOrEqual(2),
            );
            const links = screen.getAllByRole('link', { name: /back to session summary/i });
            links.forEach(link => expect(link).toHaveAttribute('href', '/puzzles'));
        });

        it('post-completion button targets /library when no session origin', async () => {
            mockSearchParams = new URLSearchParams();
            render(<LibraryPuzzle />);
            await waitFor(() => expect(screen.getByText('Reveal')).toBeInTheDocument());
            fireEvent.click(screen.getByText('Reveal'));

            // After completion both the header back-link AND the green CTA show.
            await waitFor(() =>
                expect(screen.getAllByRole('link', { name: /back to library/i }).length).toBeGreaterThanOrEqual(2),
            );
            const links = screen.getAllByRole('link', { name: /back to library/i });
            links.forEach(link => expect(link).toHaveAttribute('href', '/library'));
        });
    });
});
