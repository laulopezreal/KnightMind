import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import LibraryPuzzle from './LibraryPuzzle';

let mockUsername = 'testplayer';
let mockPuzzleId = 'puzzle-abc';

const mockNavigate = vi.fn();
vi.mock('react-router-dom', () => ({
    useParams: () => ({ puzzleId: mockPuzzleId }),
    // ConnectAccountEmpty (the no-username branch) calls this. Its absence is
    // why that branch could not be tested at all.
    useNavigate: () => mockNavigate,
    Link: ({ children, to, ...props }: { children: React.ReactNode; to: string; [key: string]: unknown }) => (
        <a href={to} {...props}>{children}</a>
    ),
}));

vi.mock('../context/ChessUsernameContext', () => ({
    useChessUsername: () => ({ username: mockUsername }),
}));

const { MockApiError, mockGetLibraryPuzzle, mockReviewPuzzle, mockGetPuzzleDiagnosis, mockGetSimilarPuzzles } = vi.hoisted(() => {
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
        mockGetSimilarPuzzles: vi.fn(),
    };
});

vi.mock('../api/core', () => ({
    ApiError: MockApiError,
}));

vi.mock('../api/puzzles', () => ({
    getLibraryPuzzle: (...args: unknown[]) => mockGetLibraryPuzzle(...args),
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
        move = vi.fn();
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
    best_move_uci: 'e2e4',
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
});
