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

const { MockApiError, mockGetLibraryPuzzle, mockReviewPuzzle, mockGetPuzzleDiagnosis } = vi.hoisted(() => {
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
        mockReviewPuzzle: vi.fn(),
        mockGetPuzzleDiagnosis: vi.fn(),
    };
});

vi.mock('../api/core', () => ({
    ApiError: MockApiError,
}));

vi.mock('../api/puzzles', () => ({
    getLibraryPuzzle: (...args: unknown[]) => mockGetLibraryPuzzle(...args),
    reviewPuzzle: (...args: unknown[]) => mockReviewPuzzle(...args),
    getPuzzleDiagnosis: (...args: unknown[]) => mockGetPuzzleDiagnosis(...args),
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
        mockGetLibraryPuzzle.mockResolvedValue(MOCK_PUZZLE);
        mockGetPuzzleDiagnosis.mockResolvedValue(MOCK_DIAGNOSIS);
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

});
