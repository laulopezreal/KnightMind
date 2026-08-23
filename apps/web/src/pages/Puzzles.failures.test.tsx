/**
 * Regression tests for honest failure handling on the puzzle surface.
 *
 * The bugs these guard against all shared one shape: a failed *request* was
 * presented as a failed *attempt*, so a network blip cost the user real
 * spaced-repetition progress.
 *   - a failed move check rendered "Not this one — take another look."
 *   - a failed reveal flipped the puzzle to 'revealed' with an empty solution,
 *     queueing it to be recorded as a self-reported fail
 *   - a failed review was swallowed, and the session advanced anyway
 *
 * The real chess.js and useClue are used so the board rollback is exercised
 * end to end.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { act, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import Puzzles from './Puzzles';
import { setupMockLocalStorage } from '../test/helpers';
import { checkPuzzle, getPuzzleDiagnosis, revealPuzzle } from '../api';
import type { UsePuzzleSessionReturn } from '../hooks/usePuzzleSession';

let mockSearchParams = new URLSearchParams();
let mockUsername = 'testplayer';
let timedOut: (() => void) | null = null;
let currentSessionReturn: UsePuzzleSessionReturn | null = null;

vi.mock('react-router-dom', () => ({
    useNavigate: () => vi.fn(),
    useSearchParams: () => [mockSearchParams, vi.fn()],
    Link: ({ children, to, ...props }: { children: React.ReactNode; to: string;[key: string]: unknown }) => (
        <a href={to} {...props}>{children}</a>
    ),
}));

vi.mock('../context/ChessUsernameContext', () => ({
    useChessUsername: () => ({ username: mockUsername, setEditorOpen: vi.fn() }),
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
    getPuzzleDiagnosis: vi.fn(),
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
    return { usePuzzleTimer: (options: { onPuzzleTimeout?: () => void }) => {
        timedOut = options.onPuzzleTimeout ?? null;
        return stub;
    } };
});

vi.mock('../components/JobStatusCard', () => ({ JobStatusCard: () => null }));
vi.mock('../components/SessionSummaryCard', () => ({ SessionSummaryCard: () => <div /> }));
vi.mock('../components/WarmupSummary', () => ({ WarmupSummary: () => <div /> }));
vi.mock('../components/AchievementsList', () => ({ AchievementsList: () => null }));
vi.mock('../components/RecentSessionsCard', () => ({ RecentSessionsCard: () => null }));
vi.mock('react-chessboard', () => ({ Chessboard: () => <div data-testid="chessboard" /> }));

const START_FEN = 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1';

const puzzle = {
    id: 'p1',
    username: 'testplayer',
    source_game_id: 'g1',
    ply: 10,
    fen: START_FEN,
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

const mockHandleReviewPuzzle = vi.fn<UsePuzzleSessionReturn['handleReviewPuzzle']>();
const mockHandleCompleteSession = vi.fn().mockResolvedValue(undefined);
const mockSetCurrentIndex = vi.fn();

function makeSessionReturn(): UsePuzzleSessionReturn {
    return {
        sessionState: 'active',
        sessionSummary: null,
        isResumingSession: false,
        streak: 0,
        bestStreak: 0,
        hintsUsed: 0,
        reviewedCount: 0,
        performanceHistory: [],
        puzzles: [puzzle, { ...puzzle, id: 'p2' }],
        currentIndex: 0,
        isLoading: false,
        error: null,
        lastFeedback: '',
        setPuzzles: vi.fn(),
        setCurrentIndex: mockSetCurrentIndex,
        setError: vi.fn(),
        setLastFeedback: vi.fn(),
        setSessionSummary: vi.fn(),
        setSessionState: vi.fn(),
        setReviewedCount: vi.fn(),
        setIsLoading: vi.fn(),
        handleStartSession: vi.fn().mockResolvedValue(undefined),
        handleCompleteSession: mockHandleCompleteSession,
        handleReviewPuzzle: mockHandleReviewPuzzle,
        handleUseHint: vi.fn().mockResolvedValue(undefined),
        calculateRecentPerformance: vi.fn().mockReturnValue(0),
        getPerformanceTrend: vi.fn().mockReturnValue('stable'),
    };
}

vi.mock('../hooks/usePuzzleSession', () => ({
    usePuzzleSession: () => currentSessionReturn ?? makeSessionReturn(),
}));

const typeAndCheck = async (user: ReturnType<typeof userEvent.setup>, move: string) => {
    await user.click(screen.getByRole('button', { name: /type move manually/i }));
    await user.type(screen.getByPlaceholderText('e.g. e2e4'), move);
    await user.click(screen.getByRole('button', { name: /check entered move/i }));
};

function deferred<T>() {
    let resolve!: (value: T) => void;
    let reject!: (reason?: unknown) => void;
    const promise = new Promise<T>((resolvePromise, rejectPromise) => {
        resolve = resolvePromise;
        reject = rejectPromise;
    });
    return { promise, resolve, reject };
}

describe('Puzzles — honest failure handling', () => {
    beforeEach(() => {
        setupMockLocalStorage();
        mockSearchParams = new URLSearchParams();
        mockUsername = 'testplayer';
        timedOut = null;
        currentSessionReturn = null;
        mockHandleReviewPuzzle.mockReset().mockResolvedValue(true);
        mockHandleCompleteSession.mockClear();
        mockSetCurrentIndex.mockClear();
        vi.mocked(revealPuzzle).mockResolvedValue({ best_move_uci: 'e2e4', solution_pv: ['e2e4'] } as never);
        vi.mocked(checkPuzzle).mockResolvedValue({ correct: true, result: 'pass' } as never);
        vi.mocked(getPuzzleDiagnosis).mockReset().mockResolvedValue({
            state: 'ready',
            puzzle_id: 'p1',
            primary_cause_label: 'Loose piece awareness',
            secondary_causes: [],
            secondary_cause_labels: [],
            evidence: [{ id: 'best.move', label: 'Best move', value: 'Qxd5' }],
            evidence_withheld: false,
        } as never);
    });

    it('does not score a network failure as a wrong answer', async () => {
        vi.mocked(checkPuzzle).mockRejectedValue(new Error('Failed to fetch'));
        const user = userEvent.setup();
        render(<Puzzles />);

        await typeAndCheck(user, 'e2e4');

        await waitFor(() =>
            expect(screen.getByRole('alert')).toHaveTextContent(/couldn't check that move/i),
        );
        expect(screen.queryByText('Not this one — take another look.')).not.toBeInTheDocument();
        // Still solving: the hint ladder and Reveal are available, and the
        // "Mark as Failed & Try Again" control (which WOULD write a fail) is not.
        expect(screen.getByRole('button', { name: /hint/i })).toBeInTheDocument();
        expect(screen.queryByText(/Mark as Failed/i)).not.toBeInTheDocument();
    });

    it('keeps the user on the puzzle when the solution cannot be loaded', async () => {
        vi.mocked(revealPuzzle).mockRejectedValue(new Error('network down'));
        const user = userEvent.setup();
        render(<Puzzles />);

        await user.click(screen.getByRole('button', { name: /reveal/i }));

        await waitFor(() =>
            expect(screen.getByRole('alert')).toHaveTextContent(/couldn't load the solution/i),
        );
        // Never flips to the revealed state (which would queue a self-reported
        // fail) and never prints an empty solution.
        expect(screen.queryByText('Solution')).not.toBeInTheDocument();
        expect(screen.getByRole('button', { name: /hint/i })).toBeInTheDocument();
    });

    it('does not advance the session when a review fails to record', async () => {
        mockHandleReviewPuzzle.mockResolvedValue(false);
        const user = userEvent.setup();
        render(<Puzzles />);

        await typeAndCheck(user, 'e2e4');
        await waitFor(() => expect(screen.getByText('Correct! Excellent.')).toBeInTheDocument());

        await user.click(screen.getByRole('button', { name: /next puzzle/i }));

        await waitFor(() =>
            expect(screen.getByRole('alert')).toHaveTextContent(/couldn't save that result/i),
        );
        expect(mockSetCurrentIndex).not.toHaveBeenCalled();
        expect(mockHandleCompleteSession).not.toHaveBeenCalled();
    });

    it('advances normally once the review is recorded', async () => {
        const user = userEvent.setup();
        render(<Puzzles />);

        await typeAndCheck(user, 'e2e4');
        await waitFor(() => expect(screen.getByText('Correct! Excellent.')).toBeInTheDocument());

        await user.click(screen.getByRole('button', { name: /next puzzle/i }));

        await waitFor(() => expect(mockSetCurrentIndex).toHaveBeenCalledWith(1));
        expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    });

    describe('a solve is recorded at the solve, not at move-on', () => {
        // The prerequisite for the post-resolution panel. The diagnosis gate
        // keys on attempts > 0, so a panel shown between the solve and the
        // move-on -- which is exactly when it should be on screen -- would
        // have asked for a diagnosis the server still considered withheld.

        it('records the pass as soon as the puzzle is solved', async () => {
            const user = userEvent.setup();
            render(<Puzzles />);

            await typeAndCheck(user, 'e2e4');

            await waitFor(() =>
                expect(mockHandleReviewPuzzle).toHaveBeenCalledWith(
                    'pass', undefined, expect.any(String),
                ),
            );
            expect(mockSetCurrentIndex).not.toHaveBeenCalled();
        });

        it('does not record a second pass when the user moves on', async () => {
            // handleReviewPuzzle rotates its idempotency key on success, so a
            // second call banks a SECOND pass and moves ease_factor twice.
            const user = userEvent.setup();
            render(<Puzzles />);

            await typeAndCheck(user, 'e2e4');
            await waitFor(() => expect(mockHandleReviewPuzzle).toHaveBeenCalledTimes(1));

            await user.click(screen.getByRole('button', { name: /next puzzle/i }));

            await waitFor(() => expect(mockSetCurrentIndex).toHaveBeenCalledWith(1));
            expect(mockHandleReviewPuzzle).toHaveBeenCalledTimes(1);
        });

        it('stays put when the solve write did not land', async () => {
            mockHandleReviewPuzzle.mockResolvedValue(false);
            const user = userEvent.setup();
            render(<Puzzles />);

            await typeAndCheck(user, 'e2e4');
            await waitFor(() => expect(mockHandleReviewPuzzle).toHaveBeenCalledTimes(1));

            await user.click(screen.getByRole('button', { name: /next puzzle/i }));

            await waitFor(() =>
                expect(screen.getByRole('alert')).toHaveTextContent(/couldn't save that result/i),
            );
            expect(mockSetCurrentIndex).not.toHaveBeenCalled();
        });

        it('ignores a duplicate solving submission and waits for its owner write before advancing', async () => {
            const pendingCheck = deferred<{ correct: boolean; result: string }>();
            const pendingReview = deferred<boolean>();
            vi.mocked(checkPuzzle).mockReturnValue(pendingCheck.promise as never);
            mockHandleReviewPuzzle.mockReturnValue(pendingReview.promise);
            const user = userEvent.setup();
            render(<Puzzles />);
            const checkCallsBeforeSubmit = vi.mocked(checkPuzzle).mock.calls.length;

            await typeAndCheck(user, 'e2e4');
            await user.click(screen.getByRole('button', { name: /check entered move/i }));

            expect(checkPuzzle).toHaveBeenCalledTimes(checkCallsBeforeSubmit + 1);
            pendingCheck.resolve({ correct: true, result: 'pass' });
            await waitFor(() => expect(mockHandleReviewPuzzle).toHaveBeenCalledTimes(1));

            await user.click(screen.getByRole('button', { name: /next puzzle/i }));
            expect(mockSetCurrentIndex).not.toHaveBeenCalled();

            pendingReview.resolve(true);
            await waitFor(() => expect(mockSetCurrentIndex).toHaveBeenCalledWith(1));
            expect(mockHandleReviewPuzzle).toHaveBeenCalledTimes(1);
        });

        it('stays on the puzzle when the owner write rejects, then retries safely', async () => {
            const pendingReview = deferred<boolean>();
            mockHandleReviewPuzzle
                .mockReturnValueOnce(pendingReview.promise)
                .mockResolvedValueOnce(true);
            const user = userEvent.setup();
            render(<Puzzles />);

            await typeAndCheck(user, 'e2e4');
            await waitFor(() => expect(mockHandleReviewPuzzle).toHaveBeenCalledTimes(1));

            await user.click(screen.getByRole('button', { name: /next puzzle/i }));
            pendingReview.reject(new Error('review unavailable'));

            await waitFor(() =>
                expect(screen.getByRole('alert')).toHaveTextContent(/couldn't save that result/i),
            );
            expect(mockSetCurrentIndex).not.toHaveBeenCalled();

            await user.click(screen.getByRole('button', { name: /next puzzle/i }));
            await waitFor(() => expect(mockSetCurrentIndex).toHaveBeenCalledWith(1));
            expect(mockHandleReviewPuzzle).toHaveBeenCalledTimes(2);
        });

        it('keeps a failed timeout decision through a delayed correct check and retries fail', async () => {
            const pendingCheck = deferred<{ correct: boolean; result: string }>();
            const pendingTimeoutReview = deferred<boolean>();
            vi.mocked(checkPuzzle).mockReturnValue(pendingCheck.promise as never);
            mockHandleReviewPuzzle
                .mockReturnValueOnce(pendingTimeoutReview.promise)
                .mockResolvedValueOnce(true);
            const user = userEvent.setup();
            render(<Puzzles />);

            await typeAndCheck(user, 'e2e4');
            expect(timedOut).not.toBeNull();
            act(() => timedOut?.());
            expect(mockHandleReviewPuzzle).toHaveBeenCalledWith('fail');

            // The timeout write fails before the delayed correct check returns.
            // The terminal fail decision must survive that failed persistence,
            // otherwise the check below turns this timed-out puzzle into a pass.
            pendingTimeoutReview.resolve(false);
            await waitFor(() => expect(screen.getByRole('button', { name: /mark as failed/i })).toBeInTheDocument());
            pendingCheck.resolve({ correct: true, result: 'pass' });
            await waitFor(() => expect(screen.queryByText('Correct! Excellent.')).not.toBeInTheDocument());
            expect(mockHandleReviewPuzzle).toHaveBeenCalledTimes(1);
            expect(mockHandleReviewPuzzle).not.toHaveBeenCalledWith('pass', expect.anything(), expect.anything());
            expect(mockSetCurrentIndex).not.toHaveBeenCalled();

            // Retry the terminal failure, rather than interpreting the delayed
            // correct check as a pass. The session hook retains its idempotency
            // key across the failed first attempt.
            await user.click(screen.getByRole('button', { name: /mark as failed/i }));
            await waitFor(() =>
                expect(screen.getByRole('alert')).toHaveTextContent(/couldn't save that result/i),
            );
            expect(mockHandleReviewPuzzle).toHaveBeenCalledTimes(1);
            await user.click(screen.getByRole('button', { name: /mark as failed/i }));
            await waitFor(() => expect(mockHandleReviewPuzzle).toHaveBeenCalledTimes(2));
            expect(mockHandleReviewPuzzle).toHaveBeenLastCalledWith('fail');
            expect(mockSetCurrentIndex).not.toHaveBeenCalled();
        });

        it('keeps a failed reveal decision through a delayed correct check', async () => {
            const pendingCheck = deferred<{ correct: boolean; result: string }>();
            const pendingRevealReview = deferred<boolean>();
            vi.mocked(checkPuzzle).mockReturnValue(pendingCheck.promise as never);
            mockHandleReviewPuzzle
                .mockReturnValueOnce(pendingRevealReview.promise)
                .mockResolvedValueOnce(true);
            const user = userEvent.setup();
            render(<Puzzles />);

            await typeAndCheck(user, 'e2e4');
            await user.click(screen.getByRole('button', { name: /reveal best move solution/i }));
            await waitFor(() => expect(mockHandleReviewPuzzle).toHaveBeenCalledWith('fail'));

            pendingRevealReview.resolve(false);
            await Promise.resolve();
            pendingCheck.resolve({ correct: true, result: 'pass' });

            await waitFor(() => expect(mockHandleReviewPuzzle).toHaveBeenCalledTimes(1));
            expect(mockHandleReviewPuzzle).not.toHaveBeenCalledWith('pass', expect.anything(), expect.anything());
            expect(screen.queryByText('Correct! Excellent.')).not.toBeInTheDocument();

            await user.click(screen.getByRole('button', { name: /next puzzle/i }));
            await waitFor(() =>
                expect(screen.getByRole('alert')).toHaveTextContent(/couldn't save that result/i),
            );
            expect(mockSetCurrentIndex).not.toHaveBeenCalled();
            await user.click(screen.getByRole('button', { name: /next puzzle/i }));
            await waitFor(() => expect(mockSetCurrentIndex).toHaveBeenCalledWith(1));
            expect(mockHandleReviewPuzzle).toHaveBeenLastCalledWith('fail');
        });

        it('ignores a delayed same-ID check response after the puzzle is rehydrated', async () => {
            const pendingCheck = deferred<{ correct: boolean; result: string }>();
            vi.mocked(checkPuzzle).mockReturnValue(pendingCheck.promise as never);
            currentSessionReturn = makeSessionReturn();
            const user = userEvent.setup();
            const { rerender } = render(<Puzzles />);

            await typeAndCheck(user, 'e2e4');
            currentSessionReturn = {
                ...currentSessionReturn,
                puzzles: [{ ...puzzle }, { ...puzzle, id: 'p2' }],
            };
            rerender(<Puzzles />);
            pendingCheck.resolve({ correct: true, result: 'pass' });

            await waitFor(() => expect(mockHandleReviewPuzzle).not.toHaveBeenCalled());
            expect(screen.queryByText('Correct! Excellent.')).not.toBeInTheDocument();
            expect(mockSetCurrentIndex).not.toHaveBeenCalled();
        });

        it('keeps the rehydrated check owner when a stale same-ID check settles', async () => {
            const checkA = deferred<{ correct: boolean; result: string }>();
            const checkB = deferred<{ correct: boolean; result: string }>();
            vi.mocked(checkPuzzle)
                .mockReturnValueOnce(checkA.promise as never)
                .mockReturnValueOnce(checkB.promise as never);
            currentSessionReturn = makeSessionReturn();
            const user = userEvent.setup();
            const { rerender } = render(<Puzzles />);
            const checkCallsBeforeSubmit = vi.mocked(checkPuzzle).mock.calls.length;

            // Check A owns the original instance. Rehydration keeps the same
            // puzzle ID but creates a distinct epoch, so Check B owns that new
            // instance while A remains in flight.
            await typeAndCheck(user, 'e2e4');
            currentSessionReturn = {
                ...currentSessionReturn,
                puzzles: [{ ...puzzle }, { ...puzzle, id: 'p2' }],
            };
            rerender(<Puzzles />);
            await user.click(screen.getByRole('button', { name: /check entered move/i }));
            expect(checkPuzzle).toHaveBeenCalledTimes(checkCallsBeforeSubmit + 2);

            // A's stale finalizer must not release B's ownership. A third
            // submission while B is pending must therefore be ignored.
            await act(async () => {
                checkA.resolve({ correct: true, result: 'pass' });
                await checkA.promise;
                await Promise.resolve();
                await Promise.resolve();
            });
            await new Promise((resolve) => setTimeout(resolve, 0));
            const input = screen.getByPlaceholderText('e.g. e2e4');
            await user.clear(input);
            await user.type(input, 'e7e5');
            await user.click(screen.getByRole('button', { name: /check entered move/i }));
            expect(checkPuzzle).toHaveBeenCalledTimes(checkCallsBeforeSubmit + 2);

            // B remains the active owner and can complete normally once its
            // own response arrives.
            checkB.resolve({ correct: true, result: 'pass' });
            await waitFor(() => expect(mockHandleReviewPuzzle).toHaveBeenCalledTimes(1));
            expect(screen.getByText('Correct! Excellent.')).toBeInTheDocument();
        });
    });

    describe('post-resolution diagnosis', () => {
        it('waits for a solved outcome write, then requests and renders diagnosis exactly once', async () => {
            const pendingReview = deferred<boolean>();
            mockHandleReviewPuzzle.mockReturnValue(pendingReview.promise);
            const user = userEvent.setup();
            render(<Puzzles />);

            await typeAndCheck(user, 'e2e4');
            await waitFor(() => expect(mockHandleReviewPuzzle).toHaveBeenCalledTimes(1));
            expect(getPuzzleDiagnosis).not.toHaveBeenCalled();
            expect(screen.queryByRole('region', { name: /mistake diagnosis/i })).not.toBeInTheDocument();

            pendingReview.resolve(true);

            await waitFor(() =>
                expect(getPuzzleDiagnosis).toHaveBeenCalledWith('p1', 'testplayer', true),
            );
            expect(await screen.findByText('Loose piece awareness')).toBeInTheDocument();
            expect(getPuzzleDiagnosis).toHaveBeenCalledTimes(1);
        });

        it('does not schedule diagnosis from a deferred terminal write after an A-to-B-to-A identity boundary', async () => {
            const pendingReview = deferred<boolean>();
            mockHandleReviewPuzzle.mockReturnValue(pendingReview.promise);
            const user = userEvent.setup();
            const { rerender } = render(<Puzzles />);

            await typeAndCheck(user, 'e2e4');
            await waitFor(() => expect(mockHandleReviewPuzzle).toHaveBeenCalledTimes(1));
            expect(getPuzzleDiagnosis).not.toHaveBeenCalled();

            mockUsername = 'otherplayer';
            rerender(<Puzzles />);
            mockUsername = 'testplayer';
            rerender(<Puzzles />);

            await act(async () => {
                pendingReview.resolve(true);
                await pendingReview.promise;
                await Promise.resolve();
                await Promise.resolve();
            });

            expect(getPuzzleDiagnosis).not.toHaveBeenCalled();
            expect(screen.queryByText('Loading diagnosis…')).not.toBeInTheDocument();
            expect(screen.queryByTestId('post-resolution-diagnosis')).not.toBeInTheDocument();
        });

        it('waits for a timeout outcome write before requesting diagnosis', async () => {
            const pendingReview = deferred<boolean>();
            mockHandleReviewPuzzle.mockReturnValue(pendingReview.promise);
            render(<Puzzles />);

            expect(timedOut).not.toBeNull();
            act(() => timedOut?.());
            await waitFor(() => expect(mockHandleReviewPuzzle).toHaveBeenCalledWith('fail'));
            expect(getPuzzleDiagnosis).not.toHaveBeenCalled();

            pendingReview.resolve(true);

            await waitFor(() =>
                expect(getPuzzleDiagnosis).toHaveBeenCalledWith('p1', 'testplayer', true),
            );
        });

        it('retries a failed final-puzzle timeout write through the diagnosis owner without blocking completion on diagnosis failure', async () => {
            mockHandleReviewPuzzle
                .mockResolvedValueOnce(false)
                .mockResolvedValueOnce(true);
            vi.mocked(getPuzzleDiagnosis).mockRejectedValue(new Error('diagnosis unavailable'));
            currentSessionReturn = {
                ...makeSessionReturn(),
                puzzles: [puzzle],
            };
            const user = userEvent.setup();
            render(<Puzzles />);

            act(() => timedOut?.());
            await waitFor(() => expect(mockHandleReviewPuzzle).toHaveBeenCalledTimes(1));
            expect(getPuzzleDiagnosis).not.toHaveBeenCalled();

            await user.click(screen.getByRole('button', { name: /finish session/i }));

            await waitFor(() => expect(mockHandleReviewPuzzle).toHaveBeenCalledTimes(2));
            await waitFor(() =>
                expect(getPuzzleDiagnosis).toHaveBeenCalledWith('p1', 'testplayer', true),
            );
            expect(getPuzzleDiagnosis).toHaveBeenCalledTimes(1);
            expect(mockHandleCompleteSession).toHaveBeenCalledTimes(1);
        });

        it('does not request diagnosis after a rejected write, then requests it once after the safe retry succeeds', async () => {
            mockHandleReviewPuzzle
                .mockResolvedValueOnce(false)
                .mockResolvedValueOnce(true);
            const user = userEvent.setup();
            render(<Puzzles />);

            await typeAndCheck(user, 'e2e4');
            await waitFor(() => expect(mockHandleReviewPuzzle).toHaveBeenCalledTimes(1));
            expect(getPuzzleDiagnosis).not.toHaveBeenCalled();

            await user.click(screen.getByRole('button', { name: /next puzzle/i }));
            await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent(/couldn't save that result/i));
            expect(getPuzzleDiagnosis).not.toHaveBeenCalled();

            await user.click(screen.getByRole('button', { name: /next puzzle/i }));
            await waitFor(() => expect(mockHandleReviewPuzzle).toHaveBeenCalledTimes(2));
            await waitFor(() => expect(getPuzzleDiagnosis).toHaveBeenCalledTimes(1));
        });

        it('does not request diagnosis for an intermediate correct ply', async () => {
            vi.mocked(checkPuzzle).mockResolvedValue({
                correct: true,
                result: 'pass',
                reply: 'e7e5',
                next_ply_index: 2,
            } as never);
            const user = userEvent.setup();
            render(<Puzzles />);

            await typeAndCheck(user, 'e2e4');

            await waitFor(() => expect(screen.getByText(/now find the next move in the line/i)).toBeInTheDocument());
            expect(mockHandleReviewPuzzle).not.toHaveBeenCalled();
            expect(getPuzzleDiagnosis).not.toHaveBeenCalled();
        });

        it('keeps the primary move-on action usable when diagnosis loading fails', async () => {
            vi.mocked(getPuzzleDiagnosis).mockRejectedValue(new Error('diagnosis unavailable'));
            const user = userEvent.setup();
            render(<Puzzles />);

            await typeAndCheck(user, 'e2e4');

            await waitFor(() => expect(getPuzzleDiagnosis).toHaveBeenCalledTimes(1));
            const nextPuzzle = screen.getByRole('button', { name: /next puzzle/i });
            expect(nextPuzzle).toBeEnabled();
            expect(screen.queryByRole('alert')).not.toBeInTheDocument();
        });

        it('keeps diagnosis compact and above the full-width primary move-on action', async () => {
            const user = userEvent.setup();
            render(<Puzzles />);

            await typeAndCheck(user, 'e2e4');

            const diagnosis = await screen.findByTestId('post-resolution-diagnosis');
            const nextPuzzle = screen.getByRole('button', { name: /next puzzle/i });
            expect(diagnosis).toHaveClass('min-w-0');
            expect(nextPuzzle).toHaveClass('w-full');
            expect(diagnosis.compareDocumentPosition(nextPuzzle) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
        });

        it('ignores a stale diagnosis response after same-ID puzzle rehydration', async () => {
            const pendingDiagnosis = deferred<unknown>();
            vi.mocked(getPuzzleDiagnosis).mockReturnValue(pendingDiagnosis.promise as never);
            currentSessionReturn = makeSessionReturn();
            const user = userEvent.setup();
            const { rerender } = render(<Puzzles />);

            await typeAndCheck(user, 'e2e4');
            await waitFor(() => expect(getPuzzleDiagnosis).toHaveBeenCalledTimes(1));

            currentSessionReturn = {
                ...currentSessionReturn,
                puzzles: [{ ...puzzle }, { ...puzzle, id: 'p2' }],
            };
            rerender(<Puzzles />);
            pendingDiagnosis.resolve({
                state: 'ready',
                puzzle_id: 'p1',
                primary_cause_label: 'Stale diagnosis',
                secondary_causes: [],
                secondary_cause_labels: [],
                evidence: [],
                evidence_withheld: false,
            });

            await waitFor(() => expect(screen.queryByText('Stale diagnosis')).not.toBeInTheDocument());
        });

        it('ignores an in-flight diagnosis when its owner crosses an A-to-B-to-A identity boundary', async () => {
            const pendingReview = deferred<boolean>();
            const pendingDiagnosis = deferred<unknown>();
            mockHandleReviewPuzzle.mockReturnValue(pendingReview.promise);
            vi.mocked(getPuzzleDiagnosis).mockReturnValue(pendingDiagnosis.promise as never);
            const user = userEvent.setup();
            const { rerender } = render(<Puzzles />);

            await typeAndCheck(user, 'e2e4');
            await waitFor(() => expect(mockHandleReviewPuzzle).toHaveBeenCalledTimes(1));
            pendingReview.resolve(true);
            await waitFor(() =>
                expect(getPuzzleDiagnosis).toHaveBeenCalledWith('p1', 'testplayer', true),
            );
            expect(screen.getByTestId('post-resolution-diagnosis')).toBeInTheDocument();

            // The terminal write remains A-owned, but its supplementary
            // diagnosis must not survive an identity round-trip on this exact
            // puzzle object. In particular, the old A finalizer cannot mutate
            // the returning A boundary after B has become active in between.
            mockUsername = 'otherplayer';
            rerender(<Puzzles />);
            mockUsername = 'testplayer';
            rerender(<Puzzles />);

            await act(async () => {
                pendingDiagnosis.resolve({
                    state: 'ready',
                    puzzle_id: 'p1',
                    primary_cause_label: 'Stale in-flight diagnosis',
                    secondary_causes: [],
                    secondary_cause_labels: [],
                    evidence: [],
                    evidence_withheld: false,
                });
                await pendingDiagnosis.promise;
            });

            await waitFor(() => {
                expect(screen.queryByText('Stale in-flight diagnosis')).not.toBeInTheDocument();
                expect(screen.queryByTestId('post-resolution-diagnosis')).not.toBeInTheDocument();
            });
            expect(getPuzzleDiagnosis).toHaveBeenCalledTimes(1);
            expect(mockHandleReviewPuzzle).toHaveBeenCalledTimes(1);
        });

        it('does not resurrect a diagnosis when the same puzzle crosses an A-to-B-to-A identity boundary', async () => {
            const user = userEvent.setup();
            const { rerender } = render(<Puzzles />);

            await typeAndCheck(user, 'e2e4');
            expect(await screen.findByText('Loose piece awareness')).toBeInTheDocument();

            mockUsername = 'otherplayer';
            rerender(<Puzzles />);
            expect(screen.queryByText('Loose piece awareness')).not.toBeInTheDocument();

            mockUsername = 'testplayer';
            rerender(<Puzzles />);
            expect(screen.queryByText('Loose piece awareness')).not.toBeInTheDocument();
            expect(getPuzzleDiagnosis).toHaveBeenCalledTimes(1);
        });
    });

    describe('a revealed solution is recorded at reveal, not at move-on', () => {
        // LibraryPuzzle has always recorded the fail inside its own reveal
        // handler. The trainer deferred it to move-on, so a user who revealed
        // and closed the tab had the attempt recorded nowhere: the answer seen
        // for free, and the scheduler never told the puzzle was failed.

        it('records the fail as soon as the solution is revealed', async () => {
            const user = userEvent.setup();
            render(<Puzzles />);

            await user.click(screen.getByRole('button', { name: /reveal/i }));

            await waitFor(() => expect(mockHandleReviewPuzzle).toHaveBeenCalledWith('fail'));
            // Before moving on: the write has already happened.
            expect(mockSetCurrentIndex).not.toHaveBeenCalled();
        });

        it('does not record a second fail when the user then moves on', async () => {
            // The regression that matters. handleReviewPuzzle rotates its
            // idempotency key on success, so a second call banks a SECOND fail
            // and moves ease_factor twice for one attempt.
            const user = userEvent.setup();
            render(<Puzzles />);

            await user.click(screen.getByRole('button', { name: /reveal/i }));
            await waitFor(() => expect(mockHandleReviewPuzzle).toHaveBeenCalledTimes(1));

            await user.click(screen.getByRole('button', { name: /next puzzle/i }));

            await waitFor(() => expect(mockSetCurrentIndex).toHaveBeenCalledWith(1));
            expect(mockHandleReviewPuzzle).toHaveBeenCalledTimes(1);
        });

        it('retries at move-on when the reveal-time write did not land', async () => {
            mockHandleReviewPuzzle.mockResolvedValue(false);
            const user = userEvent.setup();
            render(<Puzzles />);

            await user.click(screen.getByRole('button', { name: /reveal/i }));
            await waitFor(() => expect(mockHandleReviewPuzzle).toHaveBeenCalledTimes(1));

            // The write failed, so moving on must try again rather than treat
            // the puzzle as recorded and advance past it.
            // Move-on awaits the reveal's OWN write rather than firing a
            // second one, so this click surfaces that failure and stays put --
            // one click, one attempt.
            await user.click(screen.getByRole('button', { name: /next puzzle/i }));

            await waitFor(() =>
                expect(screen.getByRole('alert')).toHaveTextContent(/couldn't save that result/i),
            );
            expect(mockSetCurrentIndex).not.toHaveBeenCalled();
            expect(mockHandleReviewPuzzle).toHaveBeenCalledTimes(1);

            // The next click is the retry, with the key the hook kept.
            await user.click(screen.getByRole('button', { name: /next puzzle/i }));
            await waitFor(() => expect(mockHandleReviewPuzzle).toHaveBeenCalledTimes(2));
        });

        it('records each revealed puzzle, not just the first', async () => {
            // Asserts per-puzzle recording, NOT the ref reset. Deleting the
            // reset leaves this green, which is the honest position: the reveal
            // handler writes the flag on every reveal, so the reset is
            // defensive rather than load-bearing. Naming it after the reset
            // would have made it the seventh unfalsifiable test in this repo.
            const user = userEvent.setup();
            render(<Puzzles />);

            await user.click(screen.getByRole('button', { name: /reveal/i }));
            await waitFor(() => expect(mockHandleReviewPuzzle).toHaveBeenCalledTimes(1));
            await user.click(screen.getByRole('button', { name: /next puzzle/i }));
            await waitFor(() => expect(mockSetCurrentIndex).toHaveBeenCalledWith(1));

            await user.click(screen.getByRole('button', { name: /reveal/i }));

            await waitFor(() => expect(mockHandleReviewPuzzle).toHaveBeenCalledTimes(2));
        });

        it('does not advance on a reveal whose write never landed', async () => {
            // The race the flag ordering fixes. The flag is claimed before the
            // await, so a failed reveal-time write clears it and move-on
            // retries -- rather than reading a stale `false`, hitting the
            // session hook's in-flight guard (which returns true without
            // posting) and advancing on a write that never happened.
            mockHandleReviewPuzzle.mockResolvedValue(false);
            const user = userEvent.setup();
            render(<Puzzles />);

            await user.click(screen.getByRole('button', { name: /reveal/i }));
            await waitFor(() => expect(mockHandleReviewPuzzle).toHaveBeenCalledTimes(1));

            await user.click(screen.getByRole('button', { name: /next puzzle/i }));

            await waitFor(() =>
                expect(screen.getByRole('alert')).toHaveTextContent(/couldn't save that result/i),
            );
            expect(mockSetCurrentIndex).not.toHaveBeenCalled();
        });

        it('records nothing when the solution could not be loaded', async () => {
            // The existing guard, restated against the new write: a failed
            // request must not become a failed attempt.
            vi.mocked(revealPuzzle).mockRejectedValue(new Error('network down'));
            const user = userEvent.setup();
            render(<Puzzles />);

            await user.click(screen.getByRole('button', { name: /reveal/i }));

            await waitFor(() =>
                expect(screen.getByRole('alert')).toHaveTextContent(/couldn't load the solution/i),
            );
            expect(mockHandleReviewPuzzle).not.toHaveBeenCalled();
        });
    });

    it('renders a motif filter with its display name, plus a way to clear it', async () => {
        mockSearchParams = new URLSearchParams('motif=back_rank_mate');
        render(<Puzzles />);

        await waitFor(() =>
            expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('Back Rank Mate Puzzles'),
        );
        expect(screen.queryByText(/back_rank_mate/)).not.toBeInTheDocument();
    });
});
