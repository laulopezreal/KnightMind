import { useState } from 'react';
import { flushSync } from 'react-dom';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook, act, waitFor } from '@testing-library/react';
import { usePuzzleSession, type UsePuzzleSessionOptions, calculateRecentPerformance, getPerformanceTrend } from './usePuzzleSession';
import { setupMockLocalStorage } from '../test/helpers';

// ─── Mocks ──────────────────────────────────────────────────────────

const {
    mockStartSession,
    mockStartFocusPractice,
    mockCompleteSession,
    mockReviewPuzzle,
    mockGetSession,
    mockGetDuePuzzles,
    mockUseHint,
} = vi.hoisted(() => ({
    mockStartSession: vi.fn(),
    mockStartFocusPractice: vi.fn(),
    mockCompleteSession: vi.fn(),
    mockReviewPuzzle: vi.fn(),
    mockGetSession: vi.fn(),
    mockGetDuePuzzles: vi.fn(),
    mockUseHint: vi.fn(),
}));

vi.mock('../api', () => ({
    startSession: mockStartSession,
    startFocusPractice: mockStartFocusPractice,
    completeSession: mockCompleteSession,
    reviewPuzzle: mockReviewPuzzle,
    getSession: mockGetSession,
    getDuePuzzles: mockGetDuePuzzles,
    useHint: mockUseHint,
}));

vi.mock('../api/puzzles', () => ({
    reviewPuzzle: mockReviewPuzzle,
    getDuePuzzles: mockGetDuePuzzles,
}));

vi.mock('../api/sessions', () => ({
    startSession: mockStartSession,
    startFocusPractice: mockStartFocusPractice,
    completeSession: mockCompleteSession,
    getSession: mockGetSession,
    useHint: mockUseHint,
}));

vi.mock('../api/users', () => ({}));

import { startSession, startFocusPractice, completeSession, reviewPuzzle, getSession, getDuePuzzles, useHint, type ReviewPuzzleResponse } from '../api';

const mockedStartSession = vi.mocked(startSession);
const mockedStartFocusPractice = vi.mocked(startFocusPractice);
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
    display_name: 'Test Puzzle', used_on: null,
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
        focusCause: null,
        focusPracticeMode: false,
        focusOpening: null,
        focusOpeningScope: null,
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

function deferred<T>() {
    let resolve!: (value: T) => void;
    let reject!: (reason?: unknown) => void;
    const promise = new Promise<T>((res, rej) => {
        resolve = res;
        reject = rej;
    });
    return { promise, resolve, reject };
}

function makeFocusPracticeResponse(sessionId: string, cause = 'loose_piece_awareness') {
    return {
        session_id: sessionId,
        session_type: 'focus_practice' as const,
        focus: { cause, name: 'Loose pieces' },
        requested_n: 5,
        returned_count: 2,
        puzzles: [
            { ...mockPuzzle, review_policy: 'practice_only' as const, queue_reason: { reason: 'practice' as const, explanation: 'Extra practice.' } },
            { ...mockPuzzle2, review_policy: 'practice_only' as const, queue_reason: { reason: 'practice' as const, explanation: 'Extra practice.' } },
        ],
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

    it('starts a server-owned focus-practice snapshot without consulting the due queue', async () => {
        mockedStartFocusPractice.mockResolvedValue({
            session_id: 'focus-1',
            session_type: 'focus_practice',
            focus: { cause: 'loose_piece_awareness', name: 'Loose pieces' },
            requested_n: 5,
            returned_count: 2,
            puzzles: [
                { ...mockPuzzle, review_policy: 'practice_only', queue_reason: { reason: 'practice', explanation: 'Extra practice for your current focus.' } },
                { ...mockPuzzle2, review_policy: 'practice_only', queue_reason: { reason: 'practice', explanation: 'Extra practice for your current focus.' } },
            ],
        });
        const setActiveSessionId = vi.fn();
        const { result } = renderHook(() => usePuzzleSession(makeOpts({
            focusPracticeMode: true,
            focusCause: 'loose_piece_awareness',
            userStatus: { ...mockUserStatus, due_count: 0 },
            setActiveSessionId,
        })));

        await act(async () => {
            await result.current.handleStartSession();
        });

        expect(mockedStartFocusPractice).toHaveBeenCalledWith('testuser', 'loose_piece_awareness', 5);
        expect(mockedGetDuePuzzles).not.toHaveBeenCalled();
        expect(setActiveSessionId).toHaveBeenCalledWith('focus-1');
        expect(result.current.sessionState).toBe('active');
        expect(result.current.sessionSummary?.session_type).toBe('focus_practice');
    });

    it('keeps its own active-session transition while binding a focus-practice snapshot', async () => {
        const response = makeFocusPracticeResponse('focus-stateful');
        mockedStartFocusPractice.mockResolvedValue(response);

        const { result } = renderHook(() => {
            const [activeSessionId, setActiveSessionId] = useState<string | null>(null);
            const setActiveSessionIdAndRerender = (sessionId: string | null) => {
                flushSync(() => setActiveSessionId(sessionId));
            };
            const session = usePuzzleSession(makeOpts({
                activeSessionId,
                setActiveSessionId: setActiveSessionIdAndRerender,
                focusPracticeMode: true,
                focusCause: 'loose_piece_awareness',
                userStatus: { ...mockUserStatus, due_count: 0 },
            }));
            return { activeSessionId, session };
        });

        await act(async () => {
            await result.current.session.handleStartSession();
        });

        expect(result.current.activeSessionId).toBe('focus-stateful');
        expect(localStorage.getItem('knightmind:session:testuser')).toBe('focus-stateful');
        expect(result.current.session.sessionSummary?.session_id).toBe('focus-stateful');
        expect(result.current.session.puzzles).toEqual(response.puzzles);
        expect(result.current.session.currentIndex).toBe(0);
        expect(result.current.session.sessionState).toBe('active');
    });

    it.each([[], null])('treats a %s focus-practice resume snapshot as stale without consulting the due queue', async (snapshot) => {
        localStorage.setItem('knightmind:session:testuser', 'saved-focus-session');
        mockedGetSession.mockResolvedValue({
            ...mockSessionSummary,
            session_id: 'saved-focus-session',
            session_type: 'focus_practice',
            completed_at: null,
            puzzles: snapshot,
        } as never);
        mockedGetDuePuzzles.mockResolvedValue({
            due_count: 1,
            returned_count: 1,
            now: new Date().toISOString(),
            puzzles: [mockPuzzle],
        });
        const setActiveSessionId = vi.fn();
        const { result } = renderHook(() => usePuzzleSession(makeOpts({ setActiveSessionId })));

        await waitFor(() => expect(result.current.isResumingSession).toBe(false));

        expect(mockedGetDuePuzzles).not.toHaveBeenCalled();
        expect(result.current.puzzles).toEqual([]);
        expect(result.current.sessionState).toBe('error');
        expect(result.current.error).toMatch(/no puzzles left to review/i);
        expect(setActiveSessionId).toHaveBeenLastCalledWith(null);
    });

    it('keeps ordinary-session resume on the due queue', async () => {
        localStorage.setItem('knightmind:session:testuser', 'saved-standard-session');
        mockedGetSession.mockResolvedValue({
            ...mockSessionSummary,
            session_id: 'saved-standard-session',
            session_type: 'standard',
            completed_at: null,
        } as never);
        mockedGetDuePuzzles.mockResolvedValue({
            due_count: 1,
            returned_count: 1,
            now: new Date().toISOString(),
            puzzles: [mockPuzzle],
        });
        const { result } = renderHook(() => usePuzzleSession(makeOpts()));

        await waitFor(() => expect(result.current.sessionState).toBe('active'));
        expect(mockedGetDuePuzzles).toHaveBeenCalledTimes(1);
        expect(result.current.puzzles).toEqual([mockPuzzle]);
    });

    it('ignores stale focus-start success and finalizer after A-to-B-to-A ownership changes', async () => {
        const firstA = deferred<ReturnType<typeof makeFocusPracticeResponse>>();
        const forB = deferred<ReturnType<typeof makeFocusPracticeResponse>>();
        const secondA = deferred<ReturnType<typeof makeFocusPracticeResponse>>();
        mockedStartFocusPractice
            .mockReturnValueOnce(firstA.promise)
            .mockReturnValueOnce(forB.promise)
            .mockReturnValueOnce(secondA.promise);
        const setActiveSessionId = vi.fn();
        const initial = makeOpts({ username: 'A', focusPracticeMode: true, focusCause: 'cause-a', setActiveSessionId });
        const { result, rerender } = renderHook((options) => usePuzzleSession(options), { initialProps: initial });

        let firstStart!: Promise<void>;
        act(() => { firstStart = result.current.handleStartSession(); });
        rerender({ ...initial, username: 'B', focusCause: 'cause-b' });
        let bStart!: Promise<void>;
        act(() => { bStart = result.current.handleStartSession(); });
        rerender({ ...initial, username: 'A', focusCause: 'cause-a' });
        let secondStart!: Promise<void>;
        act(() => { secondStart = result.current.handleStartSession(); });

        await act(async () => {
            firstA.resolve(makeFocusPracticeResponse('stale-a', 'cause-a'));
            await firstStart;
        });
        expect(setActiveSessionId).not.toHaveBeenCalled();
        expect(localStorage.getItem('knightmind:session:A')).toBeNull();
        expect(result.current.isLoading).toBe(true);

        await act(async () => {
            forB.reject(new Error('stale B failure'));
            secondA.resolve(makeFocusPracticeResponse('fresh-a', 'cause-a'));
            await Promise.all([bStart, secondStart]);
        });
        expect(setActiveSessionId).toHaveBeenCalledTimes(1);
        expect(setActiveSessionId).toHaveBeenLastCalledWith('fresh-a');
        expect(localStorage.getItem('knightmind:session:A')).toBe('fresh-a');
        expect(result.current.error).toBeNull();
        expect(result.current.sessionState).toBe('active');
        expect(result.current.isLoading).toBe(false);
    });

    it('keeps a newer focus-start error when a stale success resolves', async () => {
        const stale = deferred<ReturnType<typeof makeFocusPracticeResponse>>();
        const current = deferred<ReturnType<typeof makeFocusPracticeResponse>>();
        mockedStartFocusPractice.mockReturnValueOnce(stale.promise).mockReturnValueOnce(current.promise);
        const initial = makeOpts({ username: 'A', focusPracticeMode: true, focusCause: 'cause-a' });
        const { result, rerender } = renderHook((options) => usePuzzleSession(options), { initialProps: initial });

        let staleStart!: Promise<void>;
        act(() => { staleStart = result.current.handleStartSession(); });
        rerender({ ...initial, focusCause: 'cause-b' });
        let currentStart!: Promise<void>;
        act(() => { currentStart = result.current.handleStartSession(); });
        await act(async () => {
            current.reject(new Error('current failure'));
            await currentStart;
        });
        await act(async () => {
            stale.resolve(makeFocusPracticeResponse('stale-a', 'cause-a'));
            await staleStart;
        });

        expect(result.current.sessionState).toBe('error');
        expect(result.current.error).toBe('current failure');
        expect(result.current.puzzles).toEqual([]);
        expect(localStorage.getItem('knightmind:session:A')).toBeNull();
    });

    it('ignores a focus start that resolves after unmount', async () => {
        const pending = deferred<ReturnType<typeof makeFocusPracticeResponse>>();
        mockedStartFocusPractice.mockReturnValueOnce(pending.promise);
        const setActiveSessionId = vi.fn();
        const { result, unmount } = renderHook(() => usePuzzleSession(makeOpts({
            focusPracticeMode: true,
            focusCause: 'cause-a',
            setActiveSessionId,
        })));

        let start!: Promise<void>;
        act(() => { start = result.current.handleStartSession(); });
        unmount();
        await act(async () => {
            pending.resolve(makeFocusPracticeResponse('after-unmount', 'cause-a'));
            await start;
        });

        expect(setActiveSessionId).not.toHaveBeenCalled();
        expect(localStorage.getItem('knightmind:session:testuser')).toBeNull();
    });

    it('suppresses duplicate focus-start clicks while the same operation is pending', async () => {
        const pending = deferred<ReturnType<typeof makeFocusPracticeResponse>>();
        mockedStartFocusPractice.mockReturnValueOnce(pending.promise);
        const { result } = renderHook(() => usePuzzleSession(makeOpts({ focusPracticeMode: true, focusCause: 'cause-a' })));

        let first!: Promise<void>;
        let second!: Promise<void>;
        act(() => {
            first = result.current.handleStartSession();
            second = result.current.handleStartSession();
        });
        expect(mockedStartFocusPractice).toHaveBeenCalledTimes(1);
        await act(async () => {
            pending.resolve(makeFocusPracticeResponse('one-session', 'cause-a'));
            await Promise.all([first, second]);
        });
        expect(result.current.sessionState).toBe('active');
    });

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

        expect(result.current.error).toBe('Still loading your training data — try again in a moment.');
        // The message is only rendered when the state is 'error' too — setting
        // the text alone made every start-guard failure invisible.
        expect(result.current.sessionState).toBe('error');
    });

    it('does not create a session when the puzzles fail to load', async () => {
        // Regression: the session was POSTed first, so every failed fetch left an
        // orphaned open session — and the error card's Retry (which calls back
        // into handleStartSession) minted another on every press, filling Recent
        // Sessions with empty rows and leaving an activeSessionId that could
        // neither be trained nor finished.
        mockedGetDuePuzzles.mockRejectedValue(new Error('Network down'));
        const setActiveSessionId = vi.fn();
        const { result } = renderHook(() => usePuzzleSession(makeOpts({ setActiveSessionId })));

        await act(async () => {
            await result.current.handleStartSession();
        });
        await act(async () => {
            await result.current.handleStartSession(); // the user presses Retry
        });

        expect(mockedStartSession).not.toHaveBeenCalled();
        expect(setActiveSessionId).not.toHaveBeenCalled();
        expect(result.current.sessionState).toBe('error');
        expect(result.current.error).toBe('Network down');
    });

    it('explains an empty queue instead of a blank error state', async () => {
        // Regression: an empty puzzle list set sessionState='error' with no
        // message, so the error card (which needs both) never rendered — leaving
        // an unrecoverable screen that told the user to start a session while
        // hiding the Start button.
        mockedGetDuePuzzles.mockResolvedValue({
            due_count: 0, returned_count: 0, now: new Date().toISOString(), puzzles: [],
        });
        const { result } = renderHook(() => usePuzzleSession(makeOpts()));

        await act(async () => {
            await result.current.handleStartSession();
        });

        expect(mockedStartSession).not.toHaveBeenCalled();
        expect(result.current.sessionState).toBe('error');
        expect(result.current.error).toMatch(/nothing is due right now/i);
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

        // requested_n is the number actually served, not the number asked for:
        // /puzzles/due no longer pads a short queue with not-yet-due puzzles, so
        // a 2-puzzle session must report "n / 2" rather than "n / 5".
        expect(mockedStartSession).toHaveBeenCalledWith('testuser', 2, 'standard', undefined, undefined, undefined);
        expect(setActiveSessionId).toHaveBeenCalledWith('s1');
        expect(result.current.puzzles).toHaveLength(2);
        expect(result.current.currentIndex).toBe(0);
        expect(setStatus).toHaveBeenCalledWith('solving');
        expect(result.current.sessionState).toBe('active');
    });

    it('persists the motif on the session so resume can re-serve it', async () => {
        // The write side of the resume fix: nothing else records the motif —
        // it lives only in the URL, which the nav bar drops.
        mockedStartSession.mockResolvedValue({ session_id: 's1', requested_n: 1 });
        mockedGetDuePuzzles.mockResolvedValue({
            due_count: 1,
            returned_count: 1,
            now: new Date().toISOString(),
            puzzles: [mockPuzzle],
        });

        const opts = makeOpts({ motifFilter: 'fork' });
        const { result } = renderHook(() => usePuzzleSession(opts));

        await act(async () => {
            await result.current.handleStartSession();
        });

        expect(mockedStartSession).toHaveBeenCalledWith(
            'testuser', 1, 'standard', undefined, undefined, { motif: 'fork' },
        );
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
        // Puzzles load fine here — the session POST is what fails. Stating that
        // explicitly matters: `beforeEach` calls clearAllMocks, which resets
        // recorded calls but NOT implementations, so without this line the test
        // inherits whatever the previously-run test left on getDuePuzzles and
        // asserts the wrong error message whenever the order changes.
        mockedGetDuePuzzles.mockResolvedValue({
            due_count: 1,
            returned_count: 1,
            now: new Date().toISOString(),
            puzzles: [mockPuzzle],
        });
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
        // Reviewing no longer advances session progress — that happens when the
        // user moves on from a puzzle (Puzzles.tsx), so retries can't inflate it.
        expect(result.current.reviewedCount).toBe(0);
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

    it('folds the fresh server stats back into the reviewed queue item (#Y2: stale "0/0")', async () => {
        const opts = makeOpts({ activeSessionId: 's1' });
        const { result } = renderHook(() => usePuzzleSession(opts));

        act(() => {
            result.current.setPuzzles([mockPuzzle, mockPuzzle2]);
        });

        mockedReviewPuzzle.mockResolvedValue(makeReviewResponse({
            next_due_at: '2025-03-01',
            stats: {
                attempts: 5,
                pass_count: 3,
                fail_count: 2,
                last_reviewed_at: '2025-02-01T10:00:00Z',
                last_result: 'pass',
            },
        }));

        await act(async () => {
            await result.current.handleReviewPuzzle('pass');
        });

        // The reviewed puzzle carries the server's post-review stats, so the
        // post-solve stats box can't render the stale pre-attempt numbers.
        const reviewed = result.current.puzzles.find(p => p.id === mockPuzzle.id)!;
        expect(reviewed.attempts).toBe(5);
        expect(reviewed.pass_count).toBe(3);
        expect(reviewed.fail_count).toBe(2);
        expect(reviewed.next_due_at).toBe('2025-03-01');
        // Only the reviewed item changes.
        const other = result.current.puzzles.find(p => p.id === mockPuzzle2.id)!;
        expect(other.attempts).toBeUndefined();
    });

    it('sends a client_review_id idempotency key with the review', async () => {
        const opts = makeOpts({ activeSessionId: 's1' });
        const { result } = renderHook(() => usePuzzleSession(opts));

        act(() => {
            result.current.setPuzzles([mockPuzzle, mockPuzzle2]);
        });

        mockedReviewPuzzle.mockResolvedValue(makeReviewResponse());

        await act(async () => {
            await result.current.handleReviewPuzzle('pass');
        });

        // Signature: (puzzleId, username, result, timeSpentMs, sessionId, clientReviewId, attemptedMove)
        const call = mockedReviewPuzzle.mock.calls[0];
        const clientReviewId = call[5];
        expect(typeof clientReviewId).toBe('string');
        expect((clientReviewId as string).length).toBeGreaterThan(0);
    });

    it('forwards the attempted move so the server can verify the solve', async () => {
        const opts = makeOpts({ activeSessionId: 's1' });
        const { result } = renderHook(() => usePuzzleSession(opts));

        act(() => {
            result.current.setPuzzles([mockPuzzle, mockPuzzle2]);
        });

        mockedReviewPuzzle.mockResolvedValue(makeReviewResponse());

        await act(async () => {
            await result.current.handleReviewPuzzle('pass', undefined, 'e2e4');
        });

        // attemptedMove is the 7th positional arg (index 6).
        expect(mockedReviewPuzzle.mock.calls[0][6]).toBe('e2e4');
    });

    it('trusts the server-decided result over the client claim', async () => {
        const opts = makeOpts({ activeSessionId: 's1' });
        const { result } = renderHook(() => usePuzzleSession(opts));

        act(() => {
            result.current.setPuzzles([mockPuzzle, mockPuzzle2]);
        });

        // Client claims 'pass', but the server verified the move and says 'fail'.
        mockedReviewPuzzle.mockResolvedValue(
            makeReviewResponse({ result: 'fail', verified: true, source: 'server_verified' }),
        );

        await act(async () => {
            await result.current.handleReviewPuzzle('pass', undefined, 'e2e4');
        });

        // Streak must NOT advance on a server-rejected solve, and the history
        // must record the server's outcome, not the client's claim.
        expect(result.current.streak).toBe(0);
        expect(result.current.performanceHistory.at(-1)?.result).toBe('fail');
    });

    it('in-flight guard: a concurrent double-submit fires only one POST', async () => {
        const opts = makeOpts({ activeSessionId: 's1' });
        const { result } = renderHook(() => usePuzzleSession(opts));

        act(() => {
            result.current.setPuzzles([mockPuzzle, mockPuzzle2]);
        });

        // Never-resolving promise keeps the first call "in flight"
        let resolveReview: (v: ReviewPuzzleResponse) => void = () => {};
        mockedReviewPuzzle.mockReturnValue(
            new Promise<ReviewPuzzleResponse>((res) => { resolveReview = res; }),
        );

        await act(async () => {
            // Fire two reviews without awaiting the first (double-click)
            const p1 = result.current.handleReviewPuzzle('pass');
            const p2 = result.current.handleReviewPuzzle('pass');
            resolveReview(makeReviewResponse());
            await Promise.all([p1, p2]);
        });

        expect(mockedReviewPuzzle).toHaveBeenCalledTimes(1);
    });

    it('makes concurrent callers await the owner promise instead of synthetic success', async () => {
        const opts = makeOpts({ activeSessionId: 's1' });
        const { result } = renderHook(() => usePuzzleSession(opts));
        act(() => {
            result.current.setPuzzles([mockPuzzle]);
        });

        let resolveReview: (value: ReviewPuzzleResponse) => void = () => {};
        mockedReviewPuzzle.mockReturnValue(new Promise<ReviewPuzzleResponse>((resolve) => {
            resolveReview = resolve;
        }));

        let concurrentSettled = false;
        let ownerPromise!: Promise<boolean>;
        let concurrentPromise!: Promise<boolean>;
        act(() => {
            ownerPromise = result.current.handleReviewPuzzle('fail');
            concurrentPromise = result.current.handleReviewPuzzle('pass');
            void concurrentPromise.then(() => { concurrentSettled = true; });
        });

        expect(concurrentPromise).toBe(ownerPromise);
        await Promise.resolve();
        expect(concurrentSettled).toBe(false);
        expect(mockedReviewPuzzle).toHaveBeenCalledTimes(1);
        expect(mockedReviewPuzzle).toHaveBeenCalledWith(
            mockPuzzle.id, 'testuser', 'fail', expect.any(Number), 's1', expect.any(String), undefined,
        );

        await act(async () => {
            resolveReview(makeReviewResponse({ result: 'fail' }));
            await ownerPromise;
        });
        expect(await concurrentPromise).toBe(true);
    });

    it('releases a failed owner for an idempotent retry', async () => {
        const opts = makeOpts({ activeSessionId: 's1' });
        const { result } = renderHook(() => usePuzzleSession(opts));
        act(() => {
            result.current.setPuzzles([mockPuzzle]);
        });
        mockedReviewPuzzle.mockRejectedValueOnce(new Error('network down'));
        mockedReviewPuzzle.mockResolvedValueOnce(makeReviewResponse({ result: 'fail' }));

        await act(async () => {
            const owner = result.current.handleReviewPuzzle('fail');
            expect(result.current.handleReviewPuzzle('pass')).toBe(owner);
            expect(await owner).toBe(false);
        });
        await act(async () => {
            expect(await result.current.handleReviewPuzzle('fail')).toBe(true);
        });
        expect(mockedReviewPuzzle).toHaveBeenCalledTimes(2);
        expect(mockedReviewPuzzle.mock.calls[0][5]).toBe(mockedReviewPuzzle.mock.calls[1][5]);
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

    it('does not complete the session on review (completion is driven by advancing)', async () => {
        mockedCompleteSession.mockResolvedValue(mockSessionSummary as never);

        const opts = makeOpts({ activeSessionId: 's1' });
        const { result } = renderHook(() => usePuzzleSession(opts));

        // Even a single-puzzle session must not end on review: "mark failed & try
        // again" and revealed solutions re-review the SAME puzzle, and only the
        // page's advance step ends the session. Ending on review could complete
        // before the last puzzle, stranding a dead "Next Puzzle" button.
        act(() => {
            result.current.setPuzzles([mockPuzzle]);
        });

        mockedReviewPuzzle.mockResolvedValue(makeReviewResponse());

        await act(async () => {
            await result.current.handleReviewPuzzle('pass');
        });

        expect(mockedCompleteSession).not.toHaveBeenCalled();
        expect(result.current.sessionState).not.toBe('completed');
        expect(result.current.reviewedCount).toBe(0);
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

    it('resumes using the session\'s own focus, not the URL\'s', async () => {
        // The user starts a focused session from Insights, then comes back via
        // the nav bar — no query parameter. Re-fetching with the URL's focus
        // (none) would reorder the queue, so the restored index would land on
        // a different puzzle and they would re-solve one, advancing its
        // interval a second time.
        localStorage.setItem('knightmind:session:testuser', 'saved-session');
        mockedGetSession.mockResolvedValue({
            ...mockSessionSummary,
            session_id: 'saved-session',
            completed_at: null,
            focus_cause: 'loose_piece_awareness',
        } as never);
        mockedGetDuePuzzles.mockResolvedValue({
            due_count: 1,
            returned_count: 1,
            puzzles: [mockPuzzle],
        } as never);

        const opts = makeOpts({ activeSessionId: null, focusCause: null });
        renderHook(() => usePuzzleSession(opts));

        await waitFor(() => expect(mockedGetDuePuzzles).toHaveBeenCalled());
        // The 6th argument is focusCause.
        expect(mockedGetDuePuzzles.mock.calls[0][5]).toBe('loose_piece_awareness');
    });

    it('resumes an unfocused session without a focus', async () => {
        // The mirror case: a URL focus must not leak into a session that was
        // never served with one.
        localStorage.setItem('knightmind:session:testuser', 'saved-session');
        mockedGetSession.mockResolvedValue({
            ...mockSessionSummary,
            session_id: 'saved-session',
            completed_at: null,
        } as never);
        mockedGetDuePuzzles.mockResolvedValue({
            due_count: 1,
            returned_count: 1,
            puzzles: [mockPuzzle],
        } as never);

        const opts = makeOpts({
            activeSessionId: null,
            focusCause: 'king_safety_blindness',
        });
        renderHook(() => usePuzzleSession(opts));

        await waitFor(() => expect(mockedGetDuePuzzles).toHaveBeenCalled());
        expect(mockedGetDuePuzzles.mock.calls[0][5]).toBeUndefined();
    });

    it('resumes using the session\'s own motif, not the URL\'s', async () => {
        // The user starts a motif-filtered session from /puzzles?motif=fork,
        // solves one, then comes back via the nav bar — no query parameter.
        // Re-fetching without the motif widens the queue to everything due, so
        // the restored index lands on a different puzzle and they re-solve one
        // they already did, advancing its interval a second time.
        localStorage.setItem('knightmind:session:testuser', 'saved-session');
        mockedGetSession.mockResolvedValue({
            ...mockSessionSummary,
            session_id: 'saved-session',
            completed_at: null,
            motif: 'fork',
        } as never);
        mockedGetDuePuzzles.mockResolvedValue({
            due_count: 1,
            returned_count: 1,
            puzzles: [mockPuzzle],
        } as never);

        const opts = makeOpts({ activeSessionId: null, motifFilter: null });
        renderHook(() => usePuzzleSession(opts));

        await waitFor(() => expect(mockedGetDuePuzzles).toHaveBeenCalled());
        // The 5th argument is motifFilter.
        expect(mockedGetDuePuzzles.mock.calls[0][4]).toBe('fork');
    });

    it('resumes an unfiltered session without a motif', async () => {
        // The mirror case: a stale URL motif must not narrow a session that
        // was served the full due queue.
        localStorage.setItem('knightmind:session:testuser', 'saved-session');
        mockedGetSession.mockResolvedValue({
            ...mockSessionSummary,
            session_id: 'saved-session',
            completed_at: null,
        } as never);
        mockedGetDuePuzzles.mockResolvedValue({
            due_count: 1,
            returned_count: 1,
            puzzles: [mockPuzzle],
        } as never);

        const opts = makeOpts({ activeSessionId: null, motifFilter: 'pin' });
        renderHook(() => usePuzzleSession(opts));

        await waitFor(() => expect(mockedGetDuePuzzles).toHaveBeenCalled());
        expect(mockedGetDuePuzzles.mock.calls[0][4]).toBeUndefined();
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
