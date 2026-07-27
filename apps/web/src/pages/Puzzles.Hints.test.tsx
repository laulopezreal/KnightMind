/**
 * Regression tests for the progressive hint ladder on the puzzle surface.
 *
 * The bug this guards against: during a training session the hint button used
 * to only bump a server-side counter and reveal *nothing* on the board or in
 * the status region. The fix unifies both modes onto the single `useClue`
 * ladder, so every hint press escalates the visible help:
 *   rung 1 → name the piece, rung 2 → name the destination, rung 3 → reveal.
 *
 * These tests deliberately use the REAL `useClue` hook and the REAL chess.js so
 * that the piece-naming path is exercised end to end — mocking either would
 * hide exactly the regression we care about.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, act } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import Puzzles from './Puzzles';
import { setupMockLocalStorage } from '../test/helpers';
import { useHint } from '../api';
import type { UsePuzzleSessionReturn } from '../hooks/usePuzzleSession';

// ── Module mocks (real useClue + real chess.js are intentionally NOT mocked) ──

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
    revealPuzzle: vi.fn().mockResolvedValue({ best_move_uci: 'e2e4', solution_pv: ['e2e4'] }),
    getSession: vi.fn().mockRejectedValue(new Error('No session')),
    useHint: vi.fn().mockResolvedValue({ hints_used: 1 }),
    getUserStatus: vi.fn().mockResolvedValue({ games_count: 10, puzzles_count: 5, due_count: 3, has_new_games: false }),
    getRecentSessions: vi.fn().mockResolvedValue([]),
    getMotifPerformance: vi.fn().mockResolvedValue({ motifs: [], weakest_motifs: [] }),
    cancelJob: vi.fn(),
    ApiError: class extends Error { detail?: string },
}));

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

vi.mock('../hooks/usePuzzleTimer', () => {
    // Stable identities across renders — the real hook memoizes these, and the
    // "reset clue on puzzle change" effect depends on startPuzzleTimer. A fresh
    // function each render would re-fire that effect and reset the ladder.
    const stub = {
        startPuzzleTimer: () => {},
        startSessionTimer: () => {},
        cleanup: () => {},
        currentPuzzleTime: 5,
        puzzleStartTime: null,
        timeRemaining: 0,
    };
    return { usePuzzleTimer: () => stub };
});

vi.mock('../components/JobStatusCard', () => ({ JobStatusCard: () => null }));
vi.mock('../components/SessionSummaryCard', () => ({
    SessionSummaryCard: () => <div data-testid="session-summary">Summary</div>,
}));
vi.mock('../components/WarmupSummary', () => ({
    WarmupSummary: () => <div data-testid="warmup-summary">WarmupSummary</div>,
}));
vi.mock('../components/AchievementsList', () => ({ AchievementsList: () => null }));
vi.mock('../components/RecentSessionsCard', () => ({ RecentSessionsCard: () => null }));

vi.mock('react-chessboard', () => ({
    Chessboard: () => <div data-testid="chessboard">Chessboard</div>,
}));

// Controlled mock for usePuzzleSession. We capture the props Puzzles passes in
// so a test can flip `activeSessionId` on (mirroring what handleStartSession
// does in the real hook) and observe the session-recording path.
const mockHandleUseHint = vi.fn().mockResolvedValue(undefined);
let capturedSessionProps: { setActiveSessionId: (id: string | null) => void } | undefined;
let sessionReturnOverrides: Partial<UsePuzzleSessionReturn> = {};

vi.mock('../hooks/usePuzzleSession', () => ({
    usePuzzleSession: (props: { setActiveSessionId: (id: string | null) => void }) => {
        capturedSessionProps = props;
        return makeSessionReturn(sessionReturnOverrides);
    },
}));

// ── Fixture ──────────────────────────────────────────────────────────

const puzzle = {
    id: 'p1',
    username: 'testplayer',
    source_game_id: 'g1',
    ply: 10,
    // Real starting position: e2 holds a white pawn, so rung 1 → "Move the pawn".
    fen: 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1',
    side_to_move: 'white',
    played_move_uci: 'e2e3',
    best_move_uci: 'e2e4',
    eval_before: 0.5,
    eval_after: -0.5,
    swing: 1.0,
    created_at: '2025-01-01T00:00:00Z',
    used_on: null,
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
        puzzles: [puzzle],
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
        handleReviewPuzzle: vi.fn().mockResolvedValue(undefined),
        handleUseHint: mockHandleUseHint,
        calculateRecentPerformance: vi.fn().mockReturnValue(0),
        getPerformanceTrend: vi.fn().mockReturnValue('stable'),
        ...overrides,
    };
}

// ── Tests ─────────────────────────────────────────────────────────────

describe('Puzzle hint ladder', () => {
    beforeEach(() => {
        setupMockLocalStorage();
        mockHandleUseHint.mockClear();
        vi.mocked(useHint).mockClear();
        capturedSessionProps = undefined;
        sessionReturnOverrides = {};
    });

    it('escalates visible help on each press: piece → destination', async () => {
        const user = userEvent.setup();
        render(<Puzzles />);

        const hintButton = () => screen.getByRole('button', { name: /hint/i });
        expect(hintButton()).toHaveTextContent('Hint 0/3');

        // Rung 1: name the piece to move.
        await user.click(hintButton());
        await waitFor(() => expect(screen.getByText('Move the pawn')).toBeInTheDocument());
        expect(hintButton()).toHaveTextContent('Hint 1/3');

        // Rung 2: name the destination square.
        await user.click(hintButton());
        await waitFor(() => expect(screen.getByText('Move the pawn to e4')).toBeInTheDocument());
        expect(hintButton()).toHaveTextContent('Hint 2/3');
    });

    it('reveals the full solution on the third press', async () => {
        const user = userEvent.setup();
        render(<Puzzles />);
        const hintButton = () => screen.getByRole('button', { name: /hint/i });

        await user.click(hintButton()); // rung 1
        await user.click(hintButton()); // rung 2
        await user.click(hintButton()); // rung 3 → reveal

        await waitFor(() => expect(screen.getByText('Solution')).toBeInTheDocument());
        // The revealed move is shown in human notation (SAN), not raw UCI:
        // e2e4 from the start position reads as "e4".
        expect(screen.getByText('e4')).toBeInTheDocument();
        expect(screen.queryByText('e2e4')).not.toBeInTheDocument();
    });

    it('records each hint against the session when one is active', async () => {
        const user = userEvent.setup();
        render(<Puzzles />);

        // Enter session mode the same way handleStartSession would.
        act(() => capturedSessionProps?.setActiveSessionId('s1'));

        await user.click(screen.getByRole('button', { name: /hint/i }));

        await waitFor(() => expect(mockHandleUseHint).toHaveBeenCalledTimes(1));
        // The visual reveal still happens — recording is additive, not a swap.
        expect(screen.getByText('Move the pawn')).toBeInTheDocument();
    });

    it('does not render the completed-session summary while a session is still active', async () => {
        sessionReturnOverrides = {
            sessionState: 'active',
            sessionSummary: {
                session_id: 's1',
                requested_n: 5,
                pass_count: 1,
                fail_count: 0,
                total_time_ms: 0,
                created_at: '2025-01-01T00:00:00Z',
                completed_at: null,
                session_type: 'standard',
                current_streak: 1,
                best_streak: 1,
                hints_used: 1,
            },
            reviewedCount: 1,
        };

        render(<Puzzles />);
        act(() => capturedSessionProps?.setActiveSessionId('s1'));

        await waitFor(() => expect(screen.getByTestId('mobile-puzzle-context')).toBeInTheDocument());
        expect(screen.queryByTestId('session-summary')).not.toBeInTheDocument();
    });
});
