import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import Library from './Library';

let mockUsername = 'testplayer';

const mockNavigate = vi.fn();
const mockSetEditorOpen = vi.fn();

vi.mock('react-router-dom', () => ({
    Link: ({ children, to, ...props }: { children: React.ReactNode; to: string; [key: string]: unknown }) => (
        <a href={to} {...props}>{children}</a>
    ),
    // Read lazily so the const above is initialised by the time it is called —
    // this factory runs when Library is imported, before the file's consts.
    useNavigate: () => mockNavigate,
}));

vi.mock('../context/ChessUsernameContext', () => ({
    useChessUsername: () => ({ username: mockUsername, setEditorOpen: mockSetEditorOpen }),
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
    available_causes: [],
    available_openings: [],
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
        diagnosis_summary: {
            state: 'ready' as const,
            primary_cause: 'loose_piece_awareness',
            primary_cause_label: 'Loose piece awareness',
            source: 'rules',
            diagnosed_at: '2026-01-16T12:00:00Z',
        },
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
        diagnosis_summary: null,
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

    it('should show a connect-account prompt when no username', async () => {
        mockUsername = '';
        render(<Library />);
        expect(screen.getByText('Connect your Chess.com account')).toBeInTheDocument();
        // The old prompt's button called setEditorOpen, but that editor lives
        // in UsernameDisplay, which Layout only mounts once a username exists —
        // so it could never open anything in the one state that rendered it.
        expect(screen.queryByText('Set Username')).not.toBeInTheDocument();
        expect(mockSetEditorOpen).not.toHaveBeenCalled();
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

    it('genuinely-empty library offers a Generate Puzzles button (no dead end)', async () => {
        mockGetLibraryPuzzles.mockResolvedValue(EMPTY_RESPONSE);
        render(<Library />);

        await waitFor(() => {
            expect(screen.getByText(/don't have any puzzles yet/i)).toBeInTheDocument();
        });
        expect(screen.getByRole('link', { name: 'Generate Puzzles' })).toHaveAttribute('href', '/puzzles');
    });

    it('filters-excluded-everything empty state does NOT offer Generate (puzzles exist)', async () => {
        mockGetLibraryPuzzles.mockResolvedValue({
            ...EMPTY_RESPONSE,
            stats: { total: 5, due: 0, new: 0, learning: 0, mastered: 5 },
        });
        render(<Library />);

        await waitFor(() => {
            expect(screen.getByText(/no puzzles match your filters/i)).toBeInTheDocument();
        });
        expect(screen.queryByRole('link', { name: 'Generate Puzzles' })).not.toBeInTheDocument();
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

    it('should display a compact diagnosis cause badge when present', async () => {
        render(<Library />);
        await waitFor(() => {
            expect(screen.getByText('Cause: Loose piece awareness')).toBeInTheDocument();
        });
        expect(screen.getByLabelText('Diagnosis cause')).toBeInTheDocument();
    });

    it('should not render a noisy diagnosis placeholder when missing', async () => {
        render(<Library />);
        await waitFor(() => {
            expect(screen.getByText('Knight Outpost')).toBeInTheDocument();
        });
        expect(screen.queryByText('Diagnosis unavailable')).not.toBeInTheDocument();
        expect(screen.queryByText('Cause unclear')).not.toBeInTheDocument();
    });

    it('should show "Cause unclear" badge for unclear diagnosis state', async () => {
        mockGetLibraryPuzzles.mockResolvedValue({
            ...EMPTY_RESPONSE,
            puzzles: [{
                ...MOCK_PUZZLES[0],
                diagnosis_summary: {
                    state: 'unclear' as const,
                    primary_cause: null,
                    primary_cause_label: null,
                    source: 'rules',
                    diagnosed_at: '2026-01-16T12:00:00Z',
                },
            }],
            total: 1,
            stats: MOCK_STATS,
        });
        render(<Library />);
        await waitFor(() => {
            expect(screen.getByText('Cause unclear')).toBeInTheDocument();
        });
        expect(screen.getByLabelText('Diagnosis cause')).toBeInTheDocument();
    });

    it('should show "Cause unclear" even when primary_cause_label is set (state wins)', async () => {
        mockGetLibraryPuzzles.mockResolvedValue({
            ...EMPTY_RESPONSE,
            puzzles: [{
                ...MOCK_PUZZLES[0],
                diagnosis_summary: {
                    state: 'unclear' as const,
                    primary_cause: 'loose_piece_awareness',
                    primary_cause_label: 'Loose piece awareness',
                    source: 'rules',
                    diagnosed_at: '2026-01-16T12:00:00Z',
                },
            }],
            total: 1,
            stats: MOCK_STATS,
        });
        render(<Library />);
        await waitFor(() => {
            expect(screen.getByText('Cause unclear')).toBeInTheDocument();
        });
        expect(screen.queryByText('Cause: Loose piece awareness')).not.toBeInTheDocument();
    });

    it('should show "Diagnosis unavailable" badge for unavailable diagnosis state', async () => {
        mockGetLibraryPuzzles.mockResolvedValue({
            ...EMPTY_RESPONSE,
            puzzles: [{
                ...MOCK_PUZZLES[0],
                diagnosis_summary: {
                    state: 'unavailable' as const,
                    primary_cause: null,
                    primary_cause_label: null,
                    source: null,
                    diagnosed_at: null,
                },
            }],
            total: 1,
            stats: MOCK_STATS,
        });
        render(<Library />);
        await waitFor(() => {
            expect(screen.getByText('Diagnosis unavailable')).toBeInTheDocument();
        });
        expect(screen.getByLabelText('Diagnosis cause')).toBeInTheDocument();
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

describe('Library cause filter', () => {
    const CAUSES = [
        { value: 'loose_piece_awareness', label: 'Loose piece awareness' },
        { value: 'king_safety_blindness', label: 'King safety blindness' },
    ];

    beforeEach(() => {
        vi.clearAllMocks();
        mockGetLibraryPuzzles.mockResolvedValue({
            ...EMPTY_RESPONSE,
            available_causes: CAUSES,
        });
    });

    afterEach(() => {
        window.history.replaceState({}, '', '/library');
    });

    it('applies ?cause= from the URL on first load', async () => {
        // This is the whole point of the Insights "practise this" links: they
        // arrive with the parameter already set and expect a narrowed list.
        window.history.replaceState({}, '', '/library?cause=loose_piece_awareness');
        render(<Library />);

        await waitFor(() => {
            expect(mockGetLibraryPuzzles).toHaveBeenCalledWith(
                expect.objectContaining({ cause: 'loose_piece_awareness' })
            );
        });
    });

    it('sends no cause when the URL carries none', async () => {
        render(<Library />);
        await waitFor(() => expect(mockGetLibraryPuzzles).toHaveBeenCalled());
        expect(mockGetLibraryPuzzles).toHaveBeenCalledWith(
            expect.objectContaining({ cause: undefined })
        );
    });

    it('shows the arriving cause as the selected filter', async () => {
        // Otherwise the list is narrowed with no visible reason and the user
        // cannot tell why most of their puzzles are missing.
        window.history.replaceState({}, '', '/library?cause=king_safety_blindness');
        render(<Library />);

        const select = await screen.findByLabelText('Filter by mistake cause');
        expect(select).toHaveValue('king_safety_blindness');
    });

    it('labels the options rather than showing raw slugs', async () => {
        render(<Library />);
        expect(await screen.findByRole('option', { name: 'Loose piece awareness' }))
            .toBeInTheDocument();
        expect(
            screen.queryByRole('option', { name: 'loose_piece_awareness' })
        ).not.toBeInTheDocument();
    });

    it('lets the user clear the filter they arrived with', async () => {
        window.history.replaceState({}, '', '/library?cause=loose_piece_awareness');
        render(<Library />);

        const select = await screen.findByLabelText('Filter by mistake cause');
        fireEvent.change(select, { target: { value: '' } });

        await waitFor(() => {
            expect(mockGetLibraryPuzzles).toHaveBeenLastCalledWith(
                expect.objectContaining({ cause: undefined })
            );
        });
    });

    it('hides the control when nothing has been diagnosed', async () => {
        mockGetLibraryPuzzles.mockResolvedValue(EMPTY_RESPONSE);
        render(<Library />);
        await waitFor(() => expect(mockGetLibraryPuzzles).toHaveBeenCalled());
        expect(
            screen.queryByLabelText('Filter by mistake cause')
        ).not.toBeInTheDocument();
    });
});

describe('Library phase and opening filters', () => {
    beforeEach(() => {
        vi.clearAllMocks();
        mockGetLibraryPuzzles.mockResolvedValue({
            ...EMPTY_RESPONSE,
            available_openings: ['Sicilian Defense', 'Italian Game'],
        });
    });

    afterEach(() => {
        window.history.replaceState({}, '', '/library');
    });

    it('applies ?phase= from the URL', async () => {
        window.history.replaceState({}, '', '/library?phase=endgame');
        render(<Library />);
        await waitFor(() => {
            expect(mockGetLibraryPuzzles).toHaveBeenCalledWith(
                expect.objectContaining({ phase: 'endgame' })
            );
        });
    });

    it('applies ?opening= from the URL', async () => {
        window.history.replaceState({}, '', '/library?opening=Sicilian%20Defense');
        render(<Library />);
        await waitFor(() => {
            expect(mockGetLibraryPuzzles).toHaveBeenCalledWith(
                expect.objectContaining({ opening: 'Sicilian Defense' })
            );
        });
    });

    it('offers the phases as a fixed list', async () => {
        // Phase is always populated on a diagnosis, so unlike openings there is
        // no "only offer what exists" question — all three always apply.
        render(<Library />);
        const select = await screen.findByLabelText('Filter by game phase');
        expect(select).toBeInTheDocument();
        expect(screen.getByRole('option', { name: 'Middlegame' })).toBeInTheDocument();
    });

    it('offers only openings the corpus actually contains', async () => {
        render(<Library />);
        expect(await screen.findByRole('option', { name: 'Sicilian Defense' }))
            .toBeInTheDocument();
    });

    it('hides the opening control when nothing has been classified', async () => {
        mockGetLibraryPuzzles.mockResolvedValue(EMPTY_RESPONSE);
        render(<Library />);
        await waitFor(() => expect(mockGetLibraryPuzzles).toHaveBeenCalled());
        expect(screen.queryByLabelText('Filter by opening')).not.toBeInTheDocument();
    });

    it('sends neither filter when the URL carries none', async () => {
        render(<Library />);
        await waitFor(() => expect(mockGetLibraryPuzzles).toHaveBeenCalled());
        expect(mockGetLibraryPuzzles).toHaveBeenCalledWith(
            expect.objectContaining({ phase: undefined, opening: undefined })
        );
    });
});

describe('Library opening-line filter (the Openings → Train destination)', () => {
    beforeEach(() => {
        vi.clearAllMocks();
        // Fresh object per call so a re-fetch genuinely re-renders.
        mockGetLibraryPuzzles.mockImplementation(() =>
            Promise.resolve({ ...EMPTY_RESPONSE, available_openings: [] })
        );
    });

    afterEach(() => {
        window.history.replaceState({}, '', '/library');
    });

    it('applies ?opening_line= from the URL', async () => {
        // The other half of the explorer link. Asserting the href alone in the
        // link's own tests proves nothing about the destination honouring it.
        window.history.replaceState(
            {}, '', '/library?opening_line=Sicilian%20Defense%3A%20Najdorf%20Variation'
        );
        render(<Library />);
        await waitFor(() => {
            expect(mockGetLibraryPuzzles).toHaveBeenCalledWith(
                expect.objectContaining({
                    opening_line: 'Sicilian Defense: Najdorf Variation',
                })
            );
        });
    });

    it('shows the arriving line as a removable chip', async () => {
        window.history.replaceState(
            {}, '', '/library?opening_line=Sicilian%20Defense%3A%20Najdorf%20Variation'
        );
        render(<Library />);
        expect(
            await screen.findByText('Sicilian Defense: Najdorf Variation')
        ).toBeInTheDocument();
    });

    it('lets the user clear a line they arrived with', async () => {
        window.history.replaceState(
            {}, '', '/library?opening_line=Sicilian%20Defense%3A%20Najdorf%20Variation'
        );
        render(<Library />);

        const clear = await screen.findByLabelText(/clear the .* filter/i);
        fireEvent.click(clear);

        await waitFor(() => {
            expect(mockGetLibraryPuzzles).toHaveBeenLastCalledWith(
                expect.objectContaining({ opening_line: undefined })
            );
        });
    });

    it('sends no line filter when the URL carries none', async () => {
        render(<Library />);
        await waitFor(() => expect(mockGetLibraryPuzzles).toHaveBeenCalled());
        expect(mockGetLibraryPuzzles).toHaveBeenCalledWith(
            expect.objectContaining({ opening_line: undefined })
        );
    });
});
