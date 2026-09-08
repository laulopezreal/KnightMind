/**
 * Full principal-variation (multi-move) training flow (SCORECARD dim 12 -> 9).
 *
 * A puzzle with a stored solution line is solved move-by-move: after each correct
 * move the server returns the opponent's forced reply (auto-played) and whether
 * the line is complete, WITHOUT ever sending the solver's upcoming answer. The
 * whole line is submitted for server verification only when it completes. Legacy
 * single-move puzzles keep completing on the first correct move.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import Puzzles from './Puzzles';
import { setupMockLocalStorage } from '../test/helpers';
import type { UsePuzzleSessionReturn } from '../hooks/usePuzzleSession';

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

const mockCheckPuzzle = vi.fn();

vi.mock('../api', () => ({
    generatePuzzles: vi.fn(),
    getDailyPuzzles: vi.fn().mockResolvedValue([]),
    getDuePuzzles: vi.fn().mockResolvedValue({ due_count: 0, returned_count: 0, now: '', puzzles: [] }),
    startSession: vi.fn(),
    completeSession: vi.fn(),
    reviewPuzzle: vi.fn().mockResolvedValue({}),
    checkPuzzle: (...args: unknown[]) => mockCheckPuzzle(...args),
    revealPuzzle: vi.fn().mockResolvedValue({ best_move_uci: 'd2d4', accept_moves_uci: ['d2d4'], solution_pv: ['d2d4', 'g8f6', 'c2c4', 'e7e6'] }),
    getSession: vi.fn().mockRejectedValue(new Error('No session')),
    useHint: vi.fn(),
    getUserStatus: vi.fn().mockResolvedValue({ games_count: 10, puzzles_count: 5, due_count: 3, has_new_games: false }),
    getRecentSessions: vi.fn().mockResolvedValue([]),
    getMotifPerformance: vi.fn().mockResolvedValue({ motifs: [], weakest_motifs: [] }),
    cancelJob: vi.fn(),
    ApiError: class extends Error { detail?: string },
}));

vi.mock('../api/puzzles', async () => {
    const barrel = await vi.importMock<typeof import('../api')>('../api');
    return {
        generatePuzzles: barrel.generatePuzzles,
        getDailyPuzzles: barrel.getDailyPuzzles,
        getDuePuzzles: barrel.getDuePuzzles,
        checkPuzzle: (...args: unknown[]) => mockCheckPuzzle(...args),
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
vi.mock('../components/SessionSummaryCard', () => ({ SessionSummaryCard: () => <div data-testid="session-summary">Summary</div> }));
vi.mock('../components/WarmupSummary', () => ({ WarmupSummary: () => <div data-testid="warmup-summary">WarmupSummary</div> }));
vi.mock('../components/AchievementsList', () => ({ AchievementsList: () => null }));
vi.mock('../components/RecentSessionsCard', () => ({ RecentSessionsCard: () => null }));

vi.mock('react-chessboard', () => ({
    Chessboard: () => <div data-testid="chessboard">Chessboard</div>,
}));

// A chess.js mock whose move() reflects its arguments, so a typed "d2d4" yields
// the UCI "d2d4" (the real board's legality is irrelevant here — the server
// decides correctness).
vi.mock('chess.js', () => {
    class MockChess {
        move = vi.fn((m: { from: string; to: string; promotion?: string }) => ({
            from: m.from,
            to: m.to,
            promotion: m.promotion || '',
        }));
        fen = vi.fn().mockReturnValue('rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1');
        turn = vi.fn().mockReturnValue('w');
        board = vi.fn().mockReturnValue([]);
    }
    return { Chess: MockChess };
});

const mockHandleReviewPuzzle = vi.fn().mockResolvedValue(undefined);
const mockSessionReturn = vi.fn();

vi.mock('../hooks/usePuzzleSession', () => ({
    usePuzzleSession: (...args: unknown[]) => mockSessionReturn(...args),
}));

const pvPuzzle = {
    id: 'pv-1',
    username: 'testplayer',
    source_game_id: 'g1',
    ply: 10,
    fen: 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1',
    side_to_move: 'white',
    eval_before: 0.5,
    eval_after: -0.5,
    swing: 1.0,
    created_at: '2025-01-01T00:00:00Z',
    display_name: 'Test Puzzle', used_on: null,
    attempts: 0,
    pass_count: 0,
};

function makeSessionReturn(overrides: Partial<UsePuzzleSessionReturn> = {}): UsePuzzleSessionReturn {
    return {
        sessionState: 'active',
        sessionSummary: null,
        isResumingSession: false,
        streak: 0,
        bestStreak: 0,
        hintsUsed: 0,
        reviewedCount: 0,
        performanceHistory: [],
        puzzles: [pvPuzzle],
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

async function enableTypeInput(user: ReturnType<typeof userEvent.setup>) {
    render(<Puzzles />);
    await user.click(screen.getByText('Type Move Manually'));
}

async function playMove(user: ReturnType<typeof userEvent.setup>, uci: string) {
    const input = screen.getByPlaceholderText('e.g. e2e4');
    await user.clear(input);
    await user.type(input, uci);
    await user.click(screen.getByText('Check Move'));
}

describe('Multi-move (full-PV) solve flow', () => {
    beforeEach(() => {
        vi.clearAllMocks();
        setupMockLocalStorage();
        mockSessionReturn.mockReturnValue(makeSessionReturn());
    });

    it('advances ply-by-ply through the line and completes on the last move', async () => {
        const user = userEvent.setup();
        // First move correct -> forced reply, line not complete, next ply = 2.
        mockCheckPuzzle
            .mockResolvedValueOnce({ correct: true, result: 'pass', reply: 'g8f6', complete: false, next_ply_index: 2 })
            // Second move correct -> final reply, line complete.
            .mockResolvedValueOnce({ correct: true, result: 'pass', reply: 'e7e6', complete: true, next_ply_index: null });

        await enableTypeInput(user);

        await playMove(user, 'd2d4');
        // Mid-line prompt appears; the puzzle is NOT yet solved.
        await waitFor(() =>
            expect(screen.getByText(/now find the next move in the line/i)).toBeInTheDocument()
        );
        expect(screen.queryByText('Correct! Excellent.')).not.toBeInTheDocument();

        await playMove(user, 'c2c4');
        await waitFor(() => expect(screen.getByText('Correct! Excellent.')).toBeInTheDocument());

        // The two checks carried the right ply indices (0 then 2).
        expect(mockCheckPuzzle).toHaveBeenNthCalledWith(1, 'pv-1', 'testplayer', 'd2d4', 0);
        expect(mockCheckPuzzle).toHaveBeenNthCalledWith(2, 'pv-1', 'testplayer', 'c2c4', 2);
    });

    it('submits the whole line for verification when advancing after a full solve', async () => {
        const user = userEvent.setup();
        mockCheckPuzzle
            .mockResolvedValueOnce({ correct: true, result: 'pass', reply: 'g8f6', complete: false, next_ply_index: 2 })
            .mockResolvedValueOnce({ correct: true, result: 'pass', reply: 'e7e6', complete: true, next_ply_index: null });

        await enableTypeInput(user);
        await playMove(user, 'd2d4');
        await playMove(user, 'c2c4');
        await waitFor(() => expect(screen.getByText('Correct! Excellent.')).toBeInTheDocument());

        // Advancing records the verified pass with the FULL line (space-joined).
        await user.click(screen.getByRole('button', { name: 'Finish Session' }));
        await waitFor(() =>
            expect(mockHandleReviewPuzzle).toHaveBeenCalledWith('pass', undefined, 'd2d4 c2c4')
        );
    });

    it('fails the puzzle on a wrong move mid-line', async () => {
        const user = userEvent.setup();
        mockCheckPuzzle
            .mockResolvedValueOnce({ correct: true, result: 'pass', reply: 'g8f6', complete: false, next_ply_index: 2 })
            .mockResolvedValueOnce({ correct: false, result: 'fail', reply: null, complete: false, next_ply_index: null });

        await enableTypeInput(user);
        await playMove(user, 'd2d4');
        await waitFor(() =>
            expect(screen.getByText(/now find the next move in the line/i)).toBeInTheDocument()
        );

        await playMove(user, 'b1c3');
        await waitFor(() => expect(screen.getByText('Not this one — take another look.')).toBeInTheDocument());
    });

    it('legacy single-move puzzle completes on the first correct move', async () => {
        const user = userEvent.setup();
        // No stored line: server completes on the one correct move.
        mockCheckPuzzle.mockResolvedValueOnce({ correct: true, result: 'pass', reply: null, complete: true, next_ply_index: null });

        await enableTypeInput(user);
        await playMove(user, 'd2d4');
        await waitFor(() => expect(screen.getByText('Correct! Excellent.')).toBeInTheDocument());

        expect(mockCheckPuzzle).toHaveBeenCalledTimes(1);
        expect(mockCheckPuzzle).toHaveBeenCalledWith('pv-1', 'testplayer', 'd2d4', 0);

        // The single move is what gets submitted for verification.
        await user.click(screen.getByRole('button', { name: 'Finish Session' }));
        await waitFor(() =>
            expect(mockHandleReviewPuzzle).toHaveBeenCalledWith('pass', undefined, 'd2d4')
        );
    });
});
