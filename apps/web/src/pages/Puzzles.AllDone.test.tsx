/**
 * Regression tests for issue #154:
 * Final training puzzle can leave the finish button disabled
 * after the session auto-completes (e.g. via "Mark as Failed & Try Again").
 *
 * Fix: when sessionState === 'completed' on the final puzzle, the dead finish
 * CTA is removed entirely. A meaningful alternative is shown instead:
 *   - "see your summary below" message if sessionSummary is set
 *   - "Back to Dashboard" link if no summary is available
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import Puzzles from './Puzzles';
import { setupMockLocalStorage } from '../test/helpers';
import { checkPuzzle } from '../api';
import type { UsePuzzleSessionReturn } from '../hooks/usePuzzleSession';

// ── Module mocks ─────────────────────────────────────────────────────

vi.mock('react-router-dom', () => ({
    useNavigate: () => vi.fn(),
    useSearchParams: () => [new URLSearchParams(), vi.fn()],
    Link: ({ children, to, ...props }: { children: React.ReactNode; to: string; [key: string]: unknown }) => (
        <a href={to} {...props}>{children}</a>
    ),
}));

vi.mock('../context/ChessUsernameContext', () => ({
    useChessUsername: () => ({ username: 'testplayer', setEditorOpen: vi.fn() }),
}));

vi.mock('../context/PuzzleModeContext', () => ({
    usePuzzleMode: () => ({
        sessionType: 'standard',
        targetAccuracy: 80,
        setTargetAccuracy: vi.fn(),
        targetTimeMinutes: 10,
        setTargetTimeMinutes: vi.fn(),
    }),
}));

vi.mock('../hooks/useJobPolling', () => ({
    useJobPolling: () => ({ job: null, isPolling: false }),
}));

vi.mock('../api', () => ({
    generatePuzzles: vi.fn(),
    getDailyPuzzles: vi.fn().mockResolvedValue([]),
    getDuePuzzles: vi.fn().mockResolvedValue({ due_count: 0, returned_count: 0, now: '', puzzles: [] }),
    startSession: vi.fn(),
    completeSession: vi.fn(),
    reviewPuzzle: vi.fn().mockResolvedValue({}),
    checkPuzzle: vi.fn().mockResolvedValue({ correct: true, result: 'pass' }),
    revealPuzzle: vi.fn().mockResolvedValue({ best_move_uci: 'e2e4', accept_moves_uci: ['e2e4'] }),
    getSession: vi.fn().mockRejectedValue(new Error('No session')),
    useHint: vi.fn(),
    getUserStatus: vi.fn().mockResolvedValue({ games_count: 10, puzzles_count: 5, due_count: 3, has_new_games: false }),
    getRecentSessions: vi.fn().mockResolvedValue([]),
    getMotifPerformance: vi.fn().mockResolvedValue({ motifs: [], weakest_motifs: [] }),
    cancelJob: vi.fn(),
    ApiError: class extends Error { detail?: string },
}));

// Sub-module bridges: Puzzles.tsx now imports from sub-modules directly.
// Forward to the barrel mock factory so vi.mocked() on barrel imports and
// impl calls on sub-modules share the same mock state.
vi.mock('../api/puzzles', async () => {
    const barrel = await vi.importMock<typeof import('../api')>('../api');
    return {
        generatePuzzles: barrel.generatePuzzles,
        getDailyPuzzles: barrel.getDailyPuzzles,
        getDuePuzzles: barrel.getDuePuzzles,
        checkPuzzle: barrel.checkPuzzle,
        revealPuzzle: barrel.revealPuzzle,
        reviewPuzzle: barrel.reviewPuzzle,
        requestMotifHint: vi.fn(),
        confirmPuzzleDiagnosis: vi.fn(),
        getPuzzleDiagnosis: vi.fn(),
    };
});

vi.mock('../api/ops', async () => {
    const barrel = await vi.importMock<typeof import('../api')>('../api');
    return { cancelJob: barrel.cancelJob, getJobStatus: vi.fn(), reportJobStall: vi.fn() };
});

vi.mock('../api/core', () => ({ ApiError: class extends Error { detail?: string } }));

vi.mock('../api/sessions', async () => {
    const barrel = await vi.importMock<typeof import('../api')>('../api');
    return {
        startSession: barrel.startSession,
        startFocusPractice: vi.fn(),
        completeSession: barrel.completeSession,
        getSession: barrel.getSession,
        useHint: barrel.useHint,
    };
});

vi.mock('../api/users', async () => {
    const barrel = await vi.importMock<typeof import('../api')>('../api');
    return {
        getUserStatus: barrel.getUserStatus,
        getRecentSessions: barrel.getRecentSessions,
        getMotifPerformance: barrel.getMotifPerformance,
        validateChessComUser: vi.fn(),
        importChessComGames: vi.fn(),
        getImportStatus: vi.fn(),
    };
});

vi.mock('../hooks/useAchievements', () => ({
    useAchievements: () => ({
        achievements: [],
        checkAchievements: vi.fn(),
        checkSessionAchievements: vi.fn(),
    }),
}));

vi.mock('../hooks/usePuzzleInsights', () => ({
    usePuzzleInsights: () => ({
        userStatus: { games_count: 10, puzzles_count: 5, due_count: 3, has_new_games: false },
        isLoadingStatus: false,
        motifPerformance: null,
        recentSessions: [],
        insightsError: null,
        isRefreshingInsights: false,
        refreshUserStatus: vi.fn().mockResolvedValue(undefined),
        refreshRecentSessions: vi.fn().mockResolvedValue(undefined),
        refreshMotifPerformance: vi.fn().mockResolvedValue(undefined),
        handleRefreshInsights: vi.fn(),
    }),
}));

vi.mock('../hooks/usePuzzleTimer', () => ({
    usePuzzleTimer: () => ({
        startPuzzleTimer: vi.fn(),
        startSessionTimer: vi.fn(),
        cleanup: vi.fn(),
        currentPuzzleTime: 5,
        puzzleStartTime: null,
        timeRemaining: 0,
    }),
}));

vi.mock('../hooks/useClue', () => ({
    useClue: () => ({
        clueStage: 0,
        squareStyles: {},
        pieceHint: null,
        isDisabled: false,
        advance: vi.fn(),
        reset: vi.fn(),
    }),
}));

vi.mock('../components/JobStatusCard', () => ({ JobStatusCard: () => null }));
vi.mock('../components/SessionSummaryCard', () => ({
    SessionSummaryCard: () => <div data-testid="session-summary">Summary</div>,
}));
vi.mock('../components/WarmupSummary', () => ({
    WarmupSummary: () => <div data-testid="warmup-summary">WarmupSummary</div>,
}));
vi.mock('../components/AchievementsList', () => ({ AchievementsList: () => null }));
vi.mock('../components/RecentSessionsCard', () => ({ RecentSessionsCard: () => null }));

vi.mock('../components/AccessibleChessboard', () => ({
    AccessibleChessboard: ({
        onKeyboardMove,
        options,
    }: {
        onKeyboardMove: (move: { sourceSquare: string; targetSquare: string }) => void;
        options: { onPieceDrop: (move: { sourceSquare: string; targetSquare: string }) => boolean };
    }) => (
        <div data-testid="chessboard">
            Chessboard
            <button type="button" onClick={() => onKeyboardMove({ sourceSquare: 'e2', targetSquare: 'e4' })}>
                Make keyboard board move
            </button>
            <button type="button" onClick={() => options.onPieceDrop({ sourceSquare: 'e2', targetSquare: 'e4' })}>
                Make drag board move
            </button>
        </div>
    ),
}));

vi.mock('chess.js', () => {
    class MockChess {
        move = vi.fn().mockReturnValue({ from: 'e2', to: 'e4', promotion: '' });
        fen = vi.fn().mockReturnValue('rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1');
        turn = vi.fn().mockReturnValue('w');
        board = vi.fn().mockReturnValue([]);
    }
    return { Chess: MockChess };
});

// Controlled mock for usePuzzleSession — configured per-test via mockReturn
const mockHandleReviewPuzzle = vi.fn().mockResolvedValue(undefined);
const mockSessionReturn = vi.fn();

vi.mock('../hooks/usePuzzleSession', () => ({
    usePuzzleSession: (...args: unknown[]) => mockSessionReturn(...args),
}));

// ── Fixture ──────────────────────────────────────────────────────────

const finalPuzzle = {
    id: 'p5',
    username: 'testplayer',
    source_game_id: 'g1',
    ply: 10,
    fen: 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1',
    side_to_move: 'white',
    played_move_uci: 'e2e3',
    best_move_uci: 'e2e4',
    eval_before: 0.5,
    eval_after: -0.5,
    swing: 1.0,
    created_at: '2025-01-01T00:00:00Z',
    display_name: 'Test Puzzle', used_on: null,
    attempts: 0,
    pass_count: 0,
};

// Minimal sessionSummary shape — SessionSummaryCard is mocked so the shape doesn't matter
const stubSummary = { id: 's1', session_type: 'standard', requested_n: 5 };

function makeSessionReturn(overrides: Partial<UsePuzzleSessionReturn> = {}): UsePuzzleSessionReturn {
    return {
        sessionState: 'active',
        sessionSummary: null,
        isResumingSession: false,
        streak: 0,
        bestStreak: 0,
        hintsUsed: 0,
        reviewedCount: 4,
        performanceHistory: [],
        puzzles: [finalPuzzle],
        currentIndex: 0,
        isLoading: false,
        error: null,
        lastFeedback: '',
        setPuzzles: vi.fn(),
        setCurrentIndex: vi.fn(),
        setError: vi.fn(),
        setLastFeedback: vi.fn(),
        setSessionSummary: vi.fn(),
        setSessionState: vi.fn(),
        setReviewedCount: vi.fn(),
        setIsLoading: vi.fn(),
        handleStartSession: vi.fn().mockResolvedValue(undefined),
        handleCompleteSession: vi.fn().mockResolvedValue(undefined),
        handleReviewPuzzle: mockHandleReviewPuzzle,
        handleUseHint: vi.fn().mockResolvedValue(undefined),
        calculateRecentPerformance: vi.fn().mockReturnValue(0),
        getPerformanceTrend: vi.fn().mockReturnValue('stable'),
        ...overrides,
    };
}

/** Helper: reach the post-correct state on the final puzzle. */
async function renderAndSolveCorrectly(user: ReturnType<typeof userEvent.setup>) {
    render(<Puzzles />);
    await user.click(screen.getByText('Type Move Manually'));
    const input = screen.getByPlaceholderText('e.g. e2e4');
    await user.type(input, 'e2e4');
    await user.click(screen.getByText('Check Move'));
    await waitFor(() => expect(screen.getByText('Correct! Excellent.')).toBeInTheDocument());
}

/** Helper: reach the post-incorrect state on the final puzzle. */
async function renderAndSolveIncorrectly(user: ReturnType<typeof userEvent.setup>) {
    vi.mocked(checkPuzzle).mockResolvedValueOnce({
        correct: false,
        result: 'fail',
        reply: null,
        complete: false,
        next_ply_index: null,
    });
    render(<Puzzles />);
    await user.click(screen.getByText('Type Move Manually'));
    await user.type(screen.getByPlaceholderText('e.g. e2e4'), 'e2e4');
    await user.click(screen.getByText('Check Move'));
    await waitFor(() => expect(screen.getByText('Not this one — take another look.')).toBeInTheDocument());
}

// ── Tests ─────────────────────────────────────────────────────────────

describe('Issue #154: finish button on final puzzle', () => {
    beforeEach(() => {
        vi.clearAllMocks();
        setupMockLocalStorage();
    });

    it('presents one clear finish action and folds secondary puzzle detail away', async () => {
        const user = userEvent.setup();
        mockSessionReturn.mockReturnValue(makeSessionReturn({ sessionState: 'active' }));

        await renderAndSolveCorrectly(user);

        const finishButton = screen.getByRole('button', { name: 'Finish Session' });
        expect(finishButton).not.toBeDisabled();
        expect(screen.queryByText('Input Method')).not.toBeInTheDocument();

        const details = screen.getByText('Review this puzzle').closest('details');
        expect(details).not.toHaveAttribute('open');
        expect(screen.getByText('Puzzle record')).not.toBeVisible();

        await user.click(screen.getByText('Review this puzzle'));
        expect(details).toHaveAttribute('open');
        expect(screen.getByText('Puzzle record')).toBeVisible();
    });

    it.each([
        ['keyboard', 'Make keyboard board move'],
        ['drag', 'Make drag board move'],
    ])('rejects %s board input after an incorrect final-puzzle outcome', async (_inputMethod, controlName) => {
        const user = userEvent.setup();
        mockSessionReturn.mockReturnValue(makeSessionReturn({ sessionState: 'active' }));

        await renderAndSolveIncorrectly(user);
        expect(checkPuzzle).toHaveBeenCalledTimes(1);

        fireEvent.click(screen.getByText(controlName));

        expect(checkPuzzle).toHaveBeenCalledTimes(1);
        const finishButton = screen.getByRole('button', { name: 'Finish Session' });
        expect(finishButton).not.toBeDisabled();
        expect(finishButton).toHaveClass('bg-primary');
        expect(screen.getByText('Show Solution').closest('button')).not.toHaveClass('bg-primary');
    });

    it('button is disabled and shows Recording Session while completing', async () => {
        const user = userEvent.setup();
        mockSessionReturn.mockReturnValue(makeSessionReturn({ sessionState: 'completing' }));

        await renderAndSolveCorrectly(user);

        const recordingBtn = screen.getByRole('button', { name: /Recording Session/i });
        expect(recordingBtn).toBeDisabled();
    });

    it('does not render a dead finish button when completed — shows Back to Dashboard link instead', async () => {
        const user = userEvent.setup();
        // sessionSummary: null → no summary yet → expect "Back to Dashboard" link
        mockSessionReturn.mockReturnValue(makeSessionReturn({ sessionState: 'completed', sessionSummary: null }));

        await renderAndSolveCorrectly(user);

        expect(screen.queryByRole('button', { name: 'Finish Session' })).not.toBeInTheDocument();
        const link = screen.getByRole('link', { name: 'Back to Dashboard' });
        expect(link).toBeInTheDocument();
        expect(link).toHaveAttribute('href', '/dashboard');
    });

    it('double-clicking Finish Session calls handleReviewPuzzle exactly once', async () => {
        const user = userEvent.setup();
        let resolveReview!: () => void;
        const slowReview = vi.fn().mockImplementation(
            () => new Promise<void>((resolve) => { resolveReview = resolve; })
        );
        mockSessionReturn.mockReturnValue(makeSessionReturn({
            sessionState: 'active',
            handleReviewPuzzle: slowReview,
        }));

        await renderAndSolveCorrectly(user);

        const allDoneBtn = screen.getByRole('button', { name: 'Finish Session' });
        // Fire two clicks synchronously before the async handler resolves
        fireEvent.click(allDoneBtn);
        fireEvent.click(allDoneBtn);

        resolveReview();
        await waitFor(() => expect(slowReview).toHaveBeenCalledTimes(1));
    });

    it('does not render a dead finish button when completed — shows summary message when sessionSummary is set', async () => {
        const user = userEvent.setup();
        // sessionSummary is set → SessionSummaryCard renders below; puzzle area shows completion text
        mockSessionReturn.mockReturnValue(makeSessionReturn({ sessionState: 'completed', sessionSummary: stubSummary as never }));

        await renderAndSolveCorrectly(user);

        expect(screen.queryByRole('button', { name: 'Finish Session' })).not.toBeInTheDocument();
        expect(screen.getByText(/session complete/i)).toBeInTheDocument();
        // SessionSummaryCard is rendered via the existing {sessionSummary && ...} block
        expect(screen.getByTestId('session-summary')).toBeInTheDocument();
    });
});
