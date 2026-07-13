import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import Library from './Library';

let mockUsername = 'testplayer';

vi.mock('react-router-dom', () => ({
    Link: ({ children, to, ...props }: { children: React.ReactNode; to: string; [key: string]: unknown }) => (
        <a href={to} {...props}>{children}</a>
    ),
}));

vi.mock('../context/ChessUsernameContext', () => ({
    useChessUsername: () => ({ username: mockUsername, setEditorOpen: vi.fn() }),
}));

const mockGetLibraryPuzzles = vi.fn();

vi.mock('../api/puzzles', () => ({
    getLibraryPuzzles: (...args: unknown[]) => mockGetLibraryPuzzles(...args),
}));

const MOCK_STATS = { total: 2, due: 1, new: 1, learning: 0, mastered: 0 };

const EMPTY_RESPONSE = {
    puzzles: [],
    total: 0,
    limit: 50,
    offset: 0,
    available_motifs: [],
    stats: { total: 0, due: 0, new: 0, learning: 0, mastered: 0 },
};

const MOCK_PUZZLES = [
    {
        id: 'p-1',
        title: 'Poison Pawn Trap',
        primary_motif: 'Fork',
        difficulty: 'medium' as const,
        swing: 3.0,
        fen: 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1',
        side_to_move: 'white',
        best_move_uci: 'e2e4',
        status: 'due' as const,
        attempts: 3,
        pass_count: 2,
        fail_count: 1,
        last_reviewed_at: '2026-01-15T12:00:00Z',
        last_result: 'pass',
        next_due_at: '2026-01-20T12:00:00Z',
        created_at: '2026-01-01T00:00:00Z',
    },
    {
        id: 'p-2',
        title: 'Knight Outpost',
        primary_motif: null,
        difficulty: 'easy' as const,
        swing: 1.0,
        fen: 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1',
        side_to_move: 'black',
        best_move_uci: 'd7d5',
        status: 'new' as const,
        attempts: 0,
        pass_count: 0,
        fail_count: 0,
        last_reviewed_at: null,
        last_result: null,
        next_due_at: null,
        created_at: '2026-01-05T00:00:00Z',
    },
];

describe('Library', () => {
    beforeEach(() => {
        vi.resetAllMocks();
        mockUsername = 'testplayer';
        mockGetLibraryPuzzles.mockResolvedValue({
            ...EMPTY_RESPONSE,
            puzzles: MOCK_PUZZLES,
            total: 2,
            available_motifs: ['Fork'],
            stats: MOCK_STATS,
        });
    });

    afterEach(() => {
        vi.useRealTimers();
        vi.unstubAllGlobals();
    });

    // --- No username state ---

    it('should show set username prompt when no username', async () => {
        mockUsername = '';
        render(<Library />);
        expect(screen.getByText(/Set your username/i)).toBeInTheDocument();
        expect(screen.getByRole('button', { name: /Set Username/i })).toBeInTheDocument();
    });

    it('should not fetch puzzles when no username', () => {
        mockUsername = '';
        render(<Library />);
        expect(mockGetLibraryPuzzles).not.toHaveBeenCalled();
    });

    // --- Page structure ---

    it('should render page heading', async () => {
        render(<Library />);
        expect(screen.getByText('Library')).toBeInTheDocument();
    });

    it('should render search input', async () => {
        render(<Library />);
        expect(screen.getByPlaceholderText(/Search by title or ID/i)).toBeInTheDocument();
    });

    it('should render filter dropdowns', async () => {
        render(<Library />);
        await waitFor(() => {
            const allSelects = screen.getAllByDisplayValue('All');
            expect(allSelects.length).toBeGreaterThanOrEqual(2);
        });
    });

    it('should render link to training page', async () => {
        render(<Library />);
        await waitFor(() => {
            expect(screen.getByText(/Start Training/i)).toBeInTheDocument();
        });
    });

    // --- Corpus stats ---

    it('should display corpus stats header', async () => {
        render(<Library />);
        await waitFor(() => {
            expect(screen.getByText('Total')).toBeInTheDocument();
            // Due/New/Learning/Mastered also appear in filter dropdown,
            // so check that at least 2 matches exist (stats label + dropdown)
            expect(screen.getAllByText('Due').length).toBeGreaterThanOrEqual(2);
            expect(screen.getAllByText('New').length).toBeGreaterThanOrEqual(2);
            expect(screen.getAllByText('Learning').length).toBeGreaterThanOrEqual(2);
            expect(screen.getAllByText('Mastered').length).toBeGreaterThanOrEqual(2);
        });
    });

    it('should show due count in training nudge', async () => {
        render(<Library />);
        await waitFor(() => {
            expect(screen.getByText(/1 puzzle due for review/)).toBeInTheDocument();
        });
    });

    // --- Data loading ---

    it('should call API on mount with correct params', async () => {
        render(<Library />);
        await waitFor(() => {
            expect(mockGetLibraryPuzzles).toHaveBeenCalledWith(
                expect.objectContaining({
                    username: 'testplayer',
                    sort: 'due_soonest',
                    limit: 50,
                    offset: 0,
                })
            );
        });
    });

    it('should show loading state', async () => {
        mockGetLibraryPuzzles.mockReturnValue(new Promise(() => {})); // never resolves
        render(<Library />);
        expect(screen.getAllByText(/Loading library puzzles/i).length).toBeGreaterThan(0);
    });

    it('should display puzzle titles', async () => {
        render(<Library />);
        await waitFor(() => {
            expect(screen.getByText('Poison Pawn Trap')).toBeInTheDocument();
            expect(screen.getByText('Knight Outpost')).toBeInTheDocument();
        });
    });

    it('should display status badges', async () => {
        render(<Library />);
        await waitFor(() => {
            expect(screen.getByText('due')).toBeInTheDocument();
            expect(screen.getByText('new')).toBeInTheDocument();
        });
    });

    it('should display motif badge when present', async () => {
        render(<Library />);
        await waitFor(() => {
            const matches = screen.getAllByText('Fork');
            expect(matches.length).toBeGreaterThanOrEqual(1);
        });
    });

    it('should display solve stats', async () => {
        render(<Library />);
        await waitFor(() => {
            expect(screen.getByText(/2\/3 solved/)).toBeInTheDocument();
        });
    });

    it('should show total count', async () => {
        render(<Library />);
        await waitFor(() => {
            expect(screen.getByText(/2 puzzles/)).toBeInTheDocument();
        });
    });

    it('should link puzzles to detail page', async () => {
        render(<Library />);
        await waitFor(() => {
            const links = screen.getAllByRole('link');
            const puzzleLink = links.find(l => l.getAttribute('href') === '/library/p-1');
            expect(puzzleLink).toBeTruthy();
        });
    });

    // --- Empty state ---

    it('should show an empty-corpus message when there are no puzzles and no filters', async () => {
        mockGetLibraryPuzzles.mockResolvedValue(EMPTY_RESPONSE);
        render(<Library />);
        await waitFor(() => {
            expect(screen.getByText(/don't have any puzzles yet/i)).toBeInTheDocument();
        });
        // The filter-specific copy must NOT show when no filters are active.
        expect(screen.queryByText(/No puzzles match your filters/i)).not.toBeInTheDocument();
    });

    it('should show the filter-empty message when the corpus is non-empty but nothing matches', async () => {
        // Corpus has puzzles (stats.total > 0) but the current query returned none.
        mockGetLibraryPuzzles.mockResolvedValue({
            ...EMPTY_RESPONSE,
            stats: { ...EMPTY_RESPONSE.stats, total: 12 },
        });
        render(<Library />);
        await waitFor(() => {
            expect(screen.getByText(/No puzzles match your filters/i)).toBeInTheDocument();
        });
        expect(screen.queryByText(/don't have any puzzles yet/i)).not.toBeInTheDocument();
    });

    // --- Error state ---

    it('should show error message on API failure', async () => {
        mockGetLibraryPuzzles.mockRejectedValue(new Error('Network error'));
        render(<Library />);
        await waitFor(() => {
            expect(screen.getByText(/Network error/i)).toBeInTheDocument();
        });
    });

    // --- Filter interactions ---

    it('should debounce search input', async () => {
        vi.useFakeTimers();
        render(<Library />);

        await vi.waitFor(() => {
            expect(mockGetLibraryPuzzles).toHaveBeenCalledTimes(1);
        });

        const searchInput = screen.getByPlaceholderText(/Search by title or ID/i);
        fireEvent.change(searchInput, { target: { value: 'fork' } });

        // Should not call immediately (debounce pending)
        expect(mockGetLibraryPuzzles).toHaveBeenCalledTimes(1);

        // Advance timers past the debounce delay
        await vi.advanceTimersByTimeAsync(300);

        // Now the debounced call should have been made
        await vi.waitFor(() => {
            expect(mockGetLibraryPuzzles).toHaveBeenCalledTimes(2);
            expect(mockGetLibraryPuzzles).toHaveBeenCalledWith(
                expect.objectContaining({ q: 'fork' })
            );
        });
    });

    // --- Pagination ---

    it('should show pagination when multiple pages', async () => {
        mockGetLibraryPuzzles.mockResolvedValue({
            ...EMPTY_RESPONSE,
            puzzles: MOCK_PUZZLES,
            total: 100,
        });
        render(<Library />);
        await waitFor(() => {
            expect(screen.getByText(/Page 1 of 2/)).toBeInTheDocument();
            expect(screen.getByText('Previous')).toBeInTheDocument();
            expect(screen.getByText('Next')).toBeInTheDocument();
        });
    });

    it('should not show pagination for single page', async () => {
        mockGetLibraryPuzzles.mockResolvedValue({
            ...EMPTY_RESPONSE,
            puzzles: MOCK_PUZZLES,
            total: 2,
        });
        render(<Library />);
        await waitFor(() => {
            expect(screen.getByText(/2 puzzles/)).toBeInTheDocument();
        });
        expect(screen.queryByText('Previous')).not.toBeInTheDocument();
    });

    it('should disable previous button on first page', async () => {
        mockGetLibraryPuzzles.mockResolvedValue({
            ...EMPTY_RESPONSE,
            puzzles: MOCK_PUZZLES,
            total: 100,
        });
        render(<Library />);
        await waitFor(() => {
            const prevButton = screen.getByText('Previous');
            expect(prevButton).toBeDisabled();
        });
    });
});
