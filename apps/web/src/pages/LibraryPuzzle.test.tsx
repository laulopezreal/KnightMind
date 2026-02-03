import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import LibraryPuzzle from './LibraryPuzzle';

let mockUsername = 'testplayer';
let mockPuzzleId = 'puzzle-abc';

vi.mock('react-router-dom', () => ({
    useParams: () => ({ puzzleId: mockPuzzleId }),
    Link: ({ children, to, ...props }: { children: React.ReactNode; to: string; [key: string]: unknown }) => (
        <a href={to} {...props}>{children}</a>
    ),
}));

vi.mock('../context/ChessUsernameContext', () => ({
    useChessUsername: () => ({ username: mockUsername }),
}));

const mockGetLibraryPuzzles = vi.fn();
const mockReviewPuzzle = vi.fn();

vi.mock('../api/puzzles', () => ({
    getLibraryPuzzles: (...args: unknown[]) => mockGetLibraryPuzzles(...args),
    reviewPuzzle: (...args: unknown[]) => mockReviewPuzzle(...args),
}));

vi.mock('react-chessboard', () => ({
    Chessboard: () => <div data-testid="chessboard">Chessboard</div>,
}));

vi.mock('chess.js', () => {
    class MockChess {
        load = vi.fn();
        move = vi.fn();
        fen = vi.fn().mockReturnValue('rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1');
    }
    return { Chess: MockChess };
});

const MOCK_PUZZLE = {
    id: 'puzzle-abc',
    title: 'Deadly Fork',
    primary_motif: 'Fork',
    difficulty: 'medium' as const,
    swing: 3.0,
    fen: 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1',
    side_to_move: 'white',
    best_move_uci: 'e2e4',
    status: 'due' as const,
    attempts: 5,
    pass_count: 3,
    fail_count: 2,
    last_reviewed_at: '2026-01-15T12:00:00Z',
    last_result: 'pass',
    next_due_at: '2026-01-20T12:00:00Z',
    created_at: '2026-01-01T00:00:00Z',
};

describe('LibraryPuzzle', () => {
    beforeEach(() => {
        vi.resetAllMocks();
        mockUsername = 'testplayer';
        mockPuzzleId = 'puzzle-abc';
        mockGetLibraryPuzzles.mockResolvedValue({
            puzzles: [MOCK_PUZZLE],
            total: 1,
            limit: 1,
            offset: 0,
            available_motifs: ['Fork'],
            stats: { total: 1, due: 1, new: 0, learning: 0, mastered: 0 },
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
        mockGetLibraryPuzzles.mockReturnValue(new Promise(() => {}));
        render(<LibraryPuzzle />);
        expect(screen.getByText(/Loading puzzle/i)).toBeInTheDocument();
    });

    // --- Error states ---

    it('should show error when puzzle not found', async () => {
        mockGetLibraryPuzzles.mockResolvedValue({ puzzles: [], total: 0, limit: 1, offset: 0, available_motifs: [] });
        render(<LibraryPuzzle />);
        await waitFor(() => {
            expect(screen.getByText(/Puzzle not found/i)).toBeInTheDocument();
        });
    });

    it('should show error on API failure', async () => {
        mockGetLibraryPuzzles.mockRejectedValue(new Error('Server error'));
        render(<LibraryPuzzle />);
        await waitFor(() => {
            expect(screen.getByText(/Server error/i)).toBeInTheDocument();
        });
    });

    it('should show back to library link on error', async () => {
        mockGetLibraryPuzzles.mockResolvedValue({ puzzles: [], total: 0, limit: 1, offset: 0, available_motifs: [] });
        render(<LibraryPuzzle />);
        await waitFor(() => {
            expect(screen.getByText(/Back to Library/i)).toBeInTheDocument();
        });
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

        // Should record as fail (4th arg is time_spent_ms)
        await waitFor(() => {
            expect(mockReviewPuzzle).toHaveBeenCalledWith(
                'puzzle-abc', 'testplayer', 'fail', expect.any(Number)
            );
        });
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

    // --- API call ---

    it('should search by puzzle ID to fetch detail', async () => {
        render(<LibraryPuzzle />);
        await waitFor(() => {
            expect(mockGetLibraryPuzzles).toHaveBeenCalledWith(
                expect.objectContaining({
                    username: 'testplayer',
                    q: 'puzzle-abc',
                    limit: 1,
                })
            );
        });
    });
});
