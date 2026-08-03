/**
 * Guards the page's level-one heading while a session is running.
 *
 * In-session the header block carries `hidden lg:block` to give the board room
 * on small screens — and it used to take the page's only <h1> with it, so below
 * the `lg` breakpoint the page had no level-one heading at all (axe:
 * page-has-heading-one) and its outline started at the sr-only "Your training"
 * h2. An sr-only copy of the heading now renders alongside it.
 *
 * Reaching that state needs `activeSessionId`, which is local state on the page
 * set by `usePuzzleSession` via an injected setter — so the session mock below
 * calls that setter, unlike the other Puzzles suites where it stays null and
 * the collapsed layout is never exercised.
 *
 * jsdom applies no breakpoints, so both headings are in the DOM here. The
 * invariant these assert is the one that survives that: exactly one <h1> sits
 * outside a `hidden` ancestor, which is the copy a mobile user is left with.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import Puzzles from './Puzzles';
import { setupMockLocalStorage } from '../test/helpers';

let mockSearchParams = new URLSearchParams();

vi.mock('react-router-dom', () => ({
    useNavigate: () => vi.fn(),
    useSearchParams: () => [mockSearchParams, vi.fn()],
    Link: ({ children, to, ...props }: { children: React.ReactNode; to: string;[key: string]: unknown }) => (
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
    useAchievements: () => ({ achievements: [], checkAchievements: vi.fn(), checkSessionAchievements: vi.fn() }),
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
    const stub = {
        startPuzzleTimer: () => { },
        startSessionTimer: () => { },
        cleanup: () => { },
        currentPuzzleTime: 5,
        puzzleStartTime: null,
        timeRemaining: 0,
    };
    return { usePuzzleTimer: () => stub };
});

// The session mock is the point of this file: the real hook assigns
// activeSessionId once /sessions/start resolves, and the collapsed header keys
// off it. Driven from an effect, never during render.
vi.mock('../hooks/usePuzzleSession', async () => {
    const { useEffect } = await import('react');
    const puzzle = {
        id: 'p1',
        fen: 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1',
        best_move_uci: 'e2e4',
        motif: 'fork',
        difficulty: 'medium',
        swing: 1.0,
        created_at: '2025-01-01T00:00:00Z',
        used_on: null,
        attempts: 0,
        pass_count: 0,
    };
    return {
        usePuzzleSession: ({ setActiveSessionId }: { setActiveSessionId: (id: string | null) => void }) => {
            // setActiveSessionId is a useState setter, so its identity is stable
            // and this runs once.
            useEffect(() => { setActiveSessionId('sess-1'); }, [setActiveSessionId]);
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
                handleReviewPuzzle: vi.fn(),
                handleUseHint: vi.fn().mockResolvedValue(undefined),
                calculateRecentPerformance: vi.fn().mockReturnValue(0),
                getPerformanceTrend: vi.fn().mockReturnValue('stable'),
            };
        },
    };
});

vi.mock('../components/JobStatusCard', () => ({ JobStatusCard: () => null }));
vi.mock('../components/SessionSummaryCard', () => ({ SessionSummaryCard: () => <div /> }));
vi.mock('../components/WarmupSummary', () => ({ WarmupSummary: () => <div /> }));
vi.mock('../components/AchievementsList', () => ({ AchievementsList: () => null }));
vi.mock('../components/RecentSessionsCard', () => ({ RecentSessionsCard: () => null }));
vi.mock('react-chessboard', () => ({ Chessboard: () => <div data-testid="chessboard" /> }));

/** The h1s a small-screen user is actually left with: those not inside a
 *  `hidden` ancestor. Tailwind's `lg:hidden` is a distinct class token, so it
 *  is correctly not matched by `.hidden`. */
async function headingsOutsideCollapsedBlocks() {
    return waitFor(() => {
        const outside = screen
            .getAllByRole('heading', { level: 1 })
            .filter(h => !h.closest('.hidden'));
        expect(outside.length).toBeGreaterThan(0);
        return outside;
    });
}

describe('Puzzles heading while a session is running', () => {
    beforeEach(() => {
        setupMockLocalStorage();
        mockSearchParams = new URLSearchParams();
    });

    it('keeps exactly one h1 outside the header block that collapses on mobile', async () => {
        render(<Puzzles />);

        // The collapsed layout must actually be reached, or this asserts nothing.
        await waitFor(() => {
            expect(document.querySelector('section.hidden')).not.toBeNull();
        });

        const outside = await headingsOutsideCollapsedBlocks();
        expect(outside).toHaveLength(1);
        expect(outside[0]).toHaveTextContent('Daily Puzzles');
    });

    it('gives that heading the same title as the visible one when a motif filter is active', async () => {
        // Both copies read one `pageTitle` const; this fails if they drift.
        mockSearchParams = new URLSearchParams('motif=back_rank_mate');
        render(<Puzzles />);

        const outside = await headingsOutsideCollapsedBlocks();
        expect(outside[0]).toHaveTextContent('Back Rank Mate Puzzles');
        // The raw query param must never reach the page, in either copy.
        expect(screen.queryByText(/back_rank_mate/)).not.toBeInTheDocument();
    });
});
