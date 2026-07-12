import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook, act, waitFor } from '@testing-library/react';
import { usePuzzleSession, type UsePuzzleSessionOptions, calculateRecentPerformance, getPerformanceTrend } from './usePuzzleSession';
import { setupMockLocalStorage } from '../test/helpers';

// ─── Mocks ──────────────────────────────────────────────────────────

vi.mock('../api', () => ({
    startSession: vi.fn(),
    completeSession: vi.fn(),
    reviewPuzzle: vi.fn(),
    getSession: vi.fn(),
    getDuePuzzles: vi.fn(),
    useHint: vi.fn(),
}));

import { startSession, completeSession, reviewPuzzle, getSession, getDuePuzzles, useHint, type ReviewPuzzleResponse } from '../api';

const mockedStartSession = vi.mocked(startSession);
const mockedCompleteSession = vi.mocked(completeSession);
const mockedReviewPuzzle = vi.mocked(reviewPuzzle);
const mockedGetSession = vi.mocked(getSession);
const mockedGetDuePuzzles = vi.mocked(getDuePuzzles);
const mockedUseHint = vi.mocked(useHint);

// ─── Test Fixtures ──────────────────────────────────────────────────

const mockPuzzle = {
    id: 'p1',
    username: 'testuser',
    source_game_id: 'g1',
    ply: 10,
    fen: 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1',
    side_to_move: 'white',
    played_move_uci: 'e2e4',
    best_move_uci: 'e2e4',
    eval_before: 0.3,
    eval_after: -0.5,
    swing: 0.8,
    created_at: '2025-01-01',
    used_on: null,
};

const mockPuzzle2 = { ...mockPuzzle, id: 'p2', best_move_uci: 'd2d4' };

const mockUserStatus = {
    username: 'testuser',
    games_count: 10,
    puzzles_count: 5,
    due_count: 3,
    next_due_at: null,
    has_new_games: true,
};

const mockSessionSummary = {
    session_id: 's1',
    requested_n: 5,
    pass_count: 3,
    fail_count: 2,
    total_time_ms: 60000,
    created_at: '2025-01-01T00:00:00Z',
    completed_at: '2025-01-01T00:05:00Z',
    current_streak: 2,
    best_streak: 3,
    hints_used: 1,
};

function makeReviewResponse(overrides: Partial<ReviewPuzzleResponse> = {}): ReviewPuzzleResponse {
    return {
        next_due_at: '2025-02-01',
        interval_days: 3,
        ease_factor: 2.5,
        feedback: '',
        puzzle_info: {
            fen: mockPuzzle.fen,
            best_move: mockPuzzle.best_move_uci,
            side_to_move: mockPuzzle.side_to_move,
            swing: mockPuzzle.swing,
        },
        stats: {
            attempts: 1,
            pass_count: 1,
            fail_count: 0,
            last_reviewed_at: new Date().toISOString(),
            last_result: 'pass',
        },
        ...overrides,
    };
}

function makeOpts(overrides: Partial<UsePuzzleSessionOptions> = {}): UsePuzzleSessionOptions {
    return {
        activeSessionId: null,
        setActiveSessionId: vi.fn(),
        setStatus: vi.fn(),
        username: 'testuser',
        sessionType: 'standard',
        targetAccuracy: 80,
        targetTimeMinutes: 10,
        warmupMode: false,
        motifFilter: null,
        userStatus: mockUserStatus,
        timer: {
            startSessionTimer: vi.fn(),
            cleanup: vi.fn(),
            currentPuzzleTime: 5,
            puzzleStartTime: Date.now() - 5000,
        },
        checkAchievements: vi.fn(),
        checkSessionAchievements: vi.fn(),
        refreshRecentSessions: vi.fn().mockResolvedValue(undefined),
        refreshMotifPerformance: vi.fn().mockResolvedValue(undefined),
        refreshUserStatus: vi.fn().mockResolvedValue(undefined),
        ...overrides,
    };
}

// ─── Tests ──────────────────────────────────────────────────────────

describe('usePuzzleSession', () => {
    beforeEach(() => {
        vi.clearAllMocks();
        setupMockLocalStorage();
    });

    afterEach(() => {
        vi.unstubAllGlobals();
    });

    // ── Initial state ──

    it('should have correct initial state', () => {
        const opts = makeOpts();
        const { result } = renderHook(() => usePuzzleSession(opts));

        expect(result.current.sessionState).toBe('idle');
        expect(result.current.sessionSummary).toBeNull();
        expect(result.current.isResumingSession).toBe(false);
        expect(result.current.streak).toBe(0);
        expect(result.current.puzzles).toEqual([]);
        expect(result.current.currentIndex).toBe(0);
        expect(result.current.isLoading).toBe(false);
        expect(result.current.error).toBeNull();
    });

    // ── handleStartSession ──

    it('should validate username before starting session', async () => {
        const opts = makeOpts({ username: '' });
        const { result } = renderHook(() => usePuzzleSession(opts));

        await act(async () => {
            await result.current.handleStartSession();
        });

        expect(result.current.error).toBe('Please enter a username');
        expect(mockedStartSession).not.toHaveBeenCalled();
    });

    it('should validate userStatus before starting session', async () => {
        const opts = makeOpts({ userStatus: null });
        const { result } = renderHook(() => usePuzzleSession(opts));

        await act(async () => {
            await result.current.handleStartSession();
        });

        expect(result.current.error).toBe('Loading user status...');
    });

    it('should start session and load puzzles', async () => {
        mockedStartSession.mockResolvedValue({ session_id: 's1', requested_n: 5 });
        mockedGetDuePuzzles.mockResolvedValue({
            due_count: 2,
            returned_count: 2,
            now: new Date().toISOString(),
            puzzles: [mockPuzzle, mockPuzzle2],
        });

        const setActiveSessionId = vi.fn();
        const setStatus = vi.fn();
        const opts = makeOpts({ setActiveSessionId, setStatus });
        const { result } = renderHook(() => usePuzzleSession(opts));

        await act(async () => {
            await result.current.handleStartSession();
        });

        expect(mockedStartSession).toHaveBeenCalledWith('testuser', 5, 'standard', undefined, undefined, undefined);
        expect(setActiveSessionId).toHaveBeenCalledWith('s1');
        expect(result.current.puzzles).toHaveLength(2);
        expect(result.current.currentIndex).toBe(0);
        expect(setStatus).toHaveBeenCalledWith('solving');
        expect(result.current.sessionState).toBe('active');
    });

    it('should set up timer for timed sessions', async () => {
        mockedStartSession.mockResolvedValue({ session_id: 's1', requested_n: 5 });
        mockedGetDuePuzzles.mockResolvedValue({
            due_count: 1,
            returned_count: 1,
            now: new Date().toISOString(),
            puzzles: [mockPuzzle],
        });

        const startSessionTimer = vi.fn();
        const opts = makeOpts({
            sessionType: 'timed',
            targetTimeMinutes: 10,
            timer: {
                startSessionTimer,
                cleanup: vi.fn(),
                currentPuzzleTime: 0,
                puzzleStartTime: null,
            },
        });
        const { result } = renderHook(() => usePuzzleSession(opts));

        await act(async () => {
            await result.current.handleStartSession();
        });

        expect(startSessionTimer).toHaveBeenCalledWith(600, expect.any(Function));
    });

    it('should handle startSession API error', async () => {
        mockedStartSession.mockRejectedValue(new Error('Server down'));

        const opts = makeOpts();
        const { result } = renderHook(() => usePuzzleSession(opts));

        await act(async () => {
            await result.current.handleStartSession();
        });

        expect(result.current.sessionState).toBe('error');
        expect(result.current.error).toBe('Server down');
    });

    // ── handleReviewPuzzle ──

    it('should increment streak on pass', async () => {
        const opts = makeOpts({ activeSessionId: 's1' });
        const { result } = renderHook(() => usePuzzleSession(opts));

        // Pre-load a puzzle
        act(() => {
            result.current.setPuzzles([mockPuzzle, mockPuzzle2]);
        });

        mockedReviewPuzzle.mockResolvedValue(makeReviewResponse({ feedback: 'Good job!' }));

        await act(async () => {
            await result.current.handleReviewPuzzle('pass');
        });

        expect(result.current.streak).toBe(1);
        expect(result.current.lastFeedback).toBe('Good job!');
        expect(result.current.reviewedCount).toBe(1);
    });

    it('should reset streak on fail', async () => {
        const opts = makeOpts({ activeSessionId: 's1' });
        const { result } = renderHook(() => usePuzzleSession(opts));

        act(() => {
            result.current.setPuzzles([mockPuzzle, mockPuzzle2]);
        });

        mockedReviewPuzzle.mockResolvedValue(makeReviewResponse({ interval_days: 1 }));

        // First pass to set streak to 1
        await act(async () => {
            await result.current.handleReviewPuzzle('pass');
        });
        expect(result.current.streak).toBe(1);

        // Then fail to reset
        await act(async () => {
            await result.current.handleReviewPuzzle('fail');
        });
        expect(result.current.streak).toBe(0);
    });

    it('should call checkAchievements with correct params on review', async () => {
        const checkAchievements = vi.fn();
        const opts = makeOpts({ activeSessionId: 's1', checkAchievements });
        const { result } = renderHook(() => usePuzzleSession(opts));

        act(() => {
            result.current.setPuzzles([mockPuzzle, mockPuzzle2]);
        });

        mockedReviewPuzzle.mockResolvedValue(makeReviewResponse());

        await act(async () => {
            await result.current.handleReviewPuzzle('pass');
        });

        expect(checkAchievements).toHaveBeenCalledWith({
            streak: 1,
            currentPuzzleTime: 5,
        });
    });

    it('should auto-complete session on final puzzle', async () => {
        mockedCompleteSession.mockResolvedValue(mockSessionSummary as never);

        const setActiveSessionId = vi.fn();
        const checkSessionAchievements = vi.fn();
        const opts = makeOpts({
            activeSessionId: 's1',
            setActiveSessionId,
            checkSessionAchievements,
        });
        const { result } = renderHook(() => usePuzzleSession(opts));

        // Load exactly one puzzle so reviewedCount (0 + 1) >= puzzles.length (1)
        act(() => {
            result.current.setPuzzles([mockPuzzle]);
        });

        mockedReviewPuzzle.mockResolvedValue(makeReviewResponse());

        await act(async () => {
            await result.current.handleReviewPuzzle('pass');
        });

        expect(mockedCompleteSession).toHaveBeenCalledWith('s1', 'testuser');
        expect(result.current.sessionState).toBe('completed');
        expect(checkSessionAchievements).toHaveBeenCalledWith({
            passCount: 3,
            failCount: 2,
        });
    });

    // ── handleCompleteSession ──

    it('should complete session and refresh insights', async () => {
        mockedCompleteSession.mockResolvedValue(mockSessionSummary as never);

        const setActiveSessionId = vi.fn();
        const refreshRecentSessions = vi.fn().mockResolvedValue(undefined);
        const refreshMotifPerformance = vi.fn().mockResolvedValue(undefined);
        const checkSessionAchievements = vi.fn();
        const opts = makeOpts({
            activeSessionId: 's1',
            setActiveSessionId,
            refreshRecentSessions,
            refreshMotifPerformance,
            checkSessionAchievements,
        });
        const { result } = renderHook(() => usePuzzleSession(opts));

        await act(async () => {
            await result.current.handleCompleteSession();
        });

        expect(mockedCompleteSession).toHaveBeenCalledWith('s1', 'testuser');
        expect(result.current.sessionState).toBe('completed');
        expect(setActiveSessionId).toHaveBeenCalledWith(null);
        expect(checkSessionAchievements).toHaveBeenCalled();
        expect(refreshRecentSessions).toHaveBeenCalled();
        expect(refreshMotifPerformance).toHaveBeenCalled();
    });

    // ── handleUseHint ──

    it('should call hint API and update hintsUsed', async () => {
        mockedUseHint.mockResolvedValue({ ...mockSessionSummary, hints_used: 2 } as never);

        const opts = makeOpts({ activeSessionId: 's1' });
        const { result } = renderHook(() => usePuzzleSession(opts));

        await act(async () => {
            await result.current.handleUseHint();
        });

        expect(mockedUseHint).toHaveBeenCalledWith('s1', 'testuser');
        expect(result.current.hintsUsed).toBe(2);
    });

    // ── Session resume ──

    it('should resume session from localStorage', async () => {
        localStorage.setItem('knightmind:session:testuser', 'saved-session');

        mockedGetSession.mockResolvedValue({
            ...mockSessionSummary,
            session_id: 'saved-session',
            completed_at: null,
        } as never);

        mockedGetDuePuzzles.mockResolvedValue({
            due_count: 2,
            returned_count: 2,
            now: new Date().toISOString(),
            puzzles: [mockPuzzle, mockPuzzle2],
        });

        const setActiveSessionId = vi.fn();
        const setStatus = vi.fn();
        const opts = makeOpts({ setActiveSessionId, setStatus });
        const { result } = renderHook(() => usePuzzleSession(opts));

        await waitFor(() => {
            expect(result.current.isResumingSession).toBe(false);
        });

        expect(setActiveSessionId).toHaveBeenCalledWith('saved-session');
        expect(result.current.puzzles).toHaveLength(2);
        expect(result.current.sessionState).toBe('active');
    });

    // ── Issue #154: double-completion guard ──

    it('should not call completeSession again when handleCompleteSession called with no activeSessionId', async () => {
        const opts = makeOpts({ activeSessionId: null });
        const { result } = renderHook(() => usePuzzleSession(opts));

        await act(async () => {
            await result.current.handleCompleteSession();
        });

        expect(mockedCompleteSession).not.toHaveBeenCalled();
    });

    it('sessionState is completed (not active) after final puzzle review — UI handles completed state separately', async () => {
        mockedCompleteSession.mockResolvedValue(mockSessionSummary as never);

        const opts = makeOpts({ activeSessionId: 's1' });
        const { result } = renderHook(() => usePuzzleSession(opts));

        act(() => {
            result.current.setPuzzles([mockPuzzle]);
        });

        mockedReviewPuzzle.mockResolvedValue(makeReviewResponse());

        await act(async () => {
            await result.current.handleReviewPuzzle('pass');
        });

        // Session auto-completed; sessionState is now 'completed', not 'active'
        // With the fix in Puzzles.tsx, completed final-puzzle state is rendered
        // as a post-session action instead of a disabled or no-op All Done CTA.
        expect(result.current.sessionState).toBe('completed');
        expect(mockedCompleteSession).toHaveBeenCalledTimes(1);
    });

    // ── Best streak localStorage ──

    it('should persist best streak to localStorage', async () => {
        const opts = makeOpts({ activeSessionId: 's1' });
        const { result } = renderHook(() => usePuzzleSession(opts));

        act(() => {
            result.current.setPuzzles([mockPuzzle, mockPuzzle2]);
        });

        mockedReviewPuzzle.mockResolvedValue(makeReviewResponse());

        await act(async () => {
            await result.current.handleReviewPuzzle('pass');
        });

        const saved = JSON.parse(localStorage.getItem('knightmind:puzzleStats:testuser') || '{}');
        expect(saved.bestStreak).toBe(1);
    });
});

// ─── Pure helper tests ──────────────────────────────────────────────

describe('calculateRecentPerformance', () => {
    it('should return 0 for empty history', () => {
        expect(calculateRecentPerformance([])).toBe(0);
    });

    it('should calculate accuracy for recent items', () => {
        const now = Date.now();
        const history = [
            { time: now - 60000, result: 'pass' as const },
            { time: now - 30000, result: 'pass' as const },
            { time: now - 10000, result: 'fail' as const },
        ];
        expect(calculateRecentPerformance(history)).toBe(67);
    });

    it('should exclude old items beyond the time window', () => {
        const now = Date.now();
        const history = [
            { time: now - 10 * 60 * 1000, result: 'fail' as const }, // 10 min ago, excluded
            { time: now - 1000, result: 'pass' as const },
        ];
        expect(calculateRecentPerformance(history, 5)).toBe(100);
    });
});

describe('getPerformanceTrend', () => {
    it('should return stable for short history', () => {
        expect(getPerformanceTrend([{ time: 1, result: 'pass' }])).toBe('stable');
    });

    it('should detect improving trend', () => {
        const history = [
            { time: 1, result: 'fail' as const },
            { time: 2, result: 'fail' as const },
            { time: 3, result: 'pass' as const },
            { time: 4, result: 'pass' as const },
        ];
        expect(getPerformanceTrend(history)).toBe('improving');
    });

    it('should detect declining trend', () => {
        const history = [
            { time: 1, result: 'pass' as const },
            { time: 2, result: 'pass' as const },
            { time: 3, result: 'fail' as const },
            { time: 4, result: 'fail' as const },
        ];
        expect(getPerformanceTrend(history)).toBe('declining');
    });

    it('should return stable for mixed results', () => {
        const history = [
            { time: 1, result: 'pass' as const },
            { time: 2, result: 'fail' as const },
            { time: 3, result: 'pass' as const },
            { time: 4, result: 'fail' as const },
        ];
        expect(getPerformanceTrend(history)).toBe('stable');
    });
});
