import { useCallback, useEffect, useRef, useState, type Dispatch, type SetStateAction } from 'react';
import {
    getDuePuzzles,
    startSession,
    completeSession,
    reviewPuzzle,
    getSession,
    useHint as requestHint,
    type Puzzle,
    type SessionSummary,
    type UserStatus,
} from '../api';
import type { UsePuzzleTimerReturn } from './usePuzzleTimer';

// ─── Types ──────────────────────────────────────────────────────────

export type PuzzleStatus = 'solving' | 'correct' | 'incorrect' | 'revealed';
export type SessionState = 'idle' | 'loading' | 'active' | 'completing' | 'completed' | 'error';

export interface UsePuzzleSessionOptions {
    activeSessionId: string | null;
    setActiveSessionId: (id: string | null) => void;
    setStatus: (status: PuzzleStatus) => void;
    username: string;
    sessionType: string;
    targetAccuracy: number;
    targetTimeMinutes: number;
    warmupMode: boolean;
    motifFilter: string | null;
    userStatus: UserStatus | null;
    timer: Pick<UsePuzzleTimerReturn, 'startSessionTimer' | 'cleanup' | 'currentPuzzleTime' | 'puzzleStartTime'>;
    checkAchievements: (params: { streak: number; currentPuzzleTime: number }) => void;
    checkSessionAchievements: (params: { passCount: number; failCount: number }) => void;
    refreshRecentSessions: () => Promise<void>;
    refreshMotifPerformance: () => Promise<void>;
    refreshUserStatus: () => Promise<void>;
}

export interface UsePuzzleSessionReturn {
    sessionState: SessionState;
    sessionSummary: SessionSummary | null;
    isResumingSession: boolean;
    streak: number;
    bestStreak: number;
    hintsUsed: number;
    reviewedCount: number;
    performanceHistory: Array<{ time: number; result: 'pass' | 'fail' }>;
    puzzles: Puzzle[];
    currentIndex: number;
    isLoading: boolean;
    error: string | null;
    lastFeedback: string;
    setPuzzles: Dispatch<SetStateAction<Puzzle[]>>;
    setCurrentIndex: Dispatch<SetStateAction<number>>;
    setError: Dispatch<SetStateAction<string | null>>;
    setLastFeedback: Dispatch<SetStateAction<string>>;
    setSessionSummary: Dispatch<SetStateAction<SessionSummary | null>>;
    setSessionState: Dispatch<SetStateAction<SessionState>>;
    setReviewedCount: Dispatch<SetStateAction<number>>;
    setIsLoading: Dispatch<SetStateAction<boolean>>;
    handleStartSession: () => Promise<void>;
    handleCompleteSession: () => Promise<void>;
    handleReviewPuzzle: (result: 'pass' | 'fail', timeMs?: number) => Promise<void>;
    handleUseHint: () => Promise<void>;
    calculateRecentPerformance: (history: Array<{ time: number; result: 'pass' | 'fail' }>, minutes?: number) => number;
    getPerformanceTrend: (history: Array<{ time: number; result: 'pass' | 'fail' }>) => 'improving' | 'declining' | 'stable';
}

// ─── Helpers (pure functions) ───────────────────────────────────────

export function calculateRecentPerformance(
    history: Array<{ time: number; result: 'pass' | 'fail' }>,
    minutes: number = 5,
): number {
    const cutoffTime = Date.now() - minutes * 60 * 1000;
    const recent = history.filter(item => item.time > cutoffTime);
    if (recent.length === 0) return 0;
    const passCount = recent.filter(item => item.result === 'pass').length;
    return Math.round((passCount / recent.length) * 100);
}

export function getPerformanceTrend(
    history: Array<{ time: number; result: 'pass' | 'fail' }>,
): 'improving' | 'declining' | 'stable' {
    if (history.length < 4) return 'stable';

    const recent = history.slice(-4);
    const firstHalf = recent.slice(0, 2);
    const secondHalf = recent.slice(2, 4);

    const firstHalfAccuracy = firstHalf.filter(item => item.result === 'pass').length / firstHalf.length;
    const secondHalfAccuracy = secondHalf.filter(item => item.result === 'pass').length / secondHalf.length;

    if (secondHalfAccuracy > firstHalfAccuracy + 0.1) return 'improving';
    if (secondHalfAccuracy < firstHalfAccuracy - 0.1) return 'declining';
    return 'stable';
}

// ─── Hook ───────────────────────────────────────────────────────────

export function usePuzzleSession(opts: UsePuzzleSessionOptions): UsePuzzleSessionReturn {
    const {
        activeSessionId,
        setActiveSessionId,
        setStatus,
        username,
        sessionType,
        targetAccuracy,
        targetTimeMinutes,
        warmupMode,
        motifFilter,
        userStatus,
        timer,
        checkAchievements,
        checkSessionAchievements,
        refreshRecentSessions,
        refreshMotifPerformance,
    } = opts;

    // ── State ──
    const [sessionState, setSessionState] = useState<SessionState>('idle');
    const [sessionSummary, setSessionSummary] = useState<SessionSummary | null>(null);
    const [isResumingSession, setIsResumingSession] = useState(false);
    const [streak, setStreak] = useState(0);
    const [bestStreak, setBestStreak] = useState(0);
    const [hintsUsed, setHintsUsed] = useState(0);
    const [reviewedCount, setReviewedCount] = useState(0);
    const [performanceHistory, setPerformanceHistory] = useState<Array<{ time: number; result: 'pass' | 'fail' }>>([]);
    const [puzzles, setPuzzles] = useState<Puzzle[]>([]);
    const [currentIndex, setCurrentIndex] = useState(0);
    const [isLoading, setIsLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [lastFeedback, setLastFeedback] = useState('');

    // ── localStorage: best streak ──
    useEffect(() => {
        if (!username) {
            setBestStreak(0);
            return;
        }
        const savedStats = localStorage.getItem(`knightmind:puzzleStats:${username}`);
        if (savedStats) {
            try {
                const parsed = JSON.parse(savedStats);
                setBestStreak(parsed.bestStreak || 0);
            } catch (e) {
                console.error('Failed to parse saved puzzle stats', e);
                setBestStreak(0);
            }
        } else {
            setBestStreak(0);
        }
    }, [username]);

    useEffect(() => {
        if (username) {
            localStorage.setItem(
                `knightmind:puzzleStats:${username}`,
                JSON.stringify({ bestStreak }),
            );
        }
    }, [bestStreak, username]);

    // ── localStorage: session state save ──
    useEffect(() => {
        if (username && activeSessionId) {
            const state = {
                sessionId: activeSessionId,
                currentIndex,
                streak,
                performanceHistory,
            };
            localStorage.setItem(`knightmind:sessionState:${username}`, JSON.stringify(state));
        }
    }, [username, activeSessionId, currentIndex, streak, performanceHistory]);

    // ── Session resume from localStorage ──
    useEffect(() => {
        if (!username) return;

        const savedSessionId = localStorage.getItem(`knightmind:session:${username}`);

        const loadSessionAndPuzzles = async () => {
            if (!savedSessionId) return;

            try {
                setIsResumingSession(true);
                const session = await getSession(savedSessionId);

                if (session.completed_at) {
                    localStorage.removeItem(`knightmind:session:${username}`);
                    setActiveSessionId(null);
                    return;
                }

                setActiveSessionId(session.session_id);
                setSessionSummary(session);
                setReviewedCount(session.pass_count + session.fail_count);
                setHintsUsed(session.hints_used || 0);

                // Parse saved session state once for reuse
                let parsedSessionState: { sessionId?: string; streak?: number; currentIndex?: number; performanceHistory?: Array<{ time: number; result: 'pass' | 'fail' }> } | null = null;
                const savedStateRaw = localStorage.getItem(`knightmind:sessionState:${username}`);
                if (savedStateRaw) {
                    try {
                        parsedSessionState = JSON.parse(savedStateRaw);
                    } catch (e) {
                        console.error('Failed to parse saved session state', e);
                    }
                }

                // Restore streak and performance from saved state
                if (parsedSessionState?.sessionId === session.session_id) {
                    setStreak(parsedSessionState.streak || 0);
                    if (parsedSessionState.performanceHistory) {
                        setPerformanceHistory(parsedSessionState.performanceHistory);
                    }
                }

                setSessionState('loading');
                setError(null);
                setIsLoading(true);
                try {
                    const response = await getDuePuzzles(
                        username,
                        session.requested_n,
                        session.session_type || 'standard',
                        session.target_accuracy,
                        motifFilter || undefined,
                    );
                    setPuzzles(response.puzzles);

                    // Restore current index with bounds checking
                    let restoredIndex = 0;
                    if (parsedSessionState?.sessionId === session.session_id && parsedSessionState.currentIndex !== undefined) {
                        restoredIndex = Math.min(parsedSessionState.currentIndex, response.puzzles.length - 1);
                        restoredIndex = Math.max(0, restoredIndex);
                    }
                    setCurrentIndex(restoredIndex);
                    setStatus('solving');
                    if (response.puzzles.length > 0) {
                        setSessionState('active');
                        setError(null);
                    } else {
                        setSessionState('error');
                    }
                } catch (err) {
                    const message = err instanceof Error ? err.message : 'Failed to load puzzles';
                    setError(message);
                    setSessionState('error');
                } finally {
                    setIsLoading(false);
                }
            } catch (err) {
                console.error('Failed to resume session:', err);
                localStorage.removeItem(`knightmind:session:${username}`);
                setActiveSessionId(null);
                setSessionState('idle');
            } finally {
                setIsResumingSession(false);
            }
        };

        loadSessionAndPuzzles();
        // eslint-disable-next-line react-hooks/exhaustive-deps -- run only on username change
    }, [username]);

    // ── handleCompleteSession ──
    const handleCompleteSession = useCallback(async () => {
        if (!activeSessionId || !username.trim()) return;

        setSessionState('completing');
        setError(null);

        timer.cleanup();

        try {
            const summary = await completeSession(activeSessionId, username.trim());
            setSessionSummary(summary);
            setActiveSessionId(null);
            localStorage.removeItem(`knightmind:session:${username.trim()}`);
            localStorage.removeItem(`knightmind:sessionState:${username.trim()}`);
            setSessionState('completed');

            checkSessionAchievements({ passCount: summary.pass_count, failCount: summary.fail_count });

            await Promise.all([refreshRecentSessions(), refreshMotifPerformance()]);
        } catch (err) {
            console.error('Failed to complete session:', err);
            const errorMessage = err instanceof Error ? err.message : 'Failed to complete session. Please try again.';
            setError(errorMessage);
            setSessionState('active');
        }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- use timer.cleanup, not the unstable timer object
    }, [activeSessionId, checkSessionAchievements, refreshMotifPerformance, refreshRecentSessions, setActiveSessionId, timer.cleanup, username]);

    // ── handleReviewPuzzle ──
    const currentPuzzle = puzzles[currentIndex];
    const handleReviewPuzzle = useCallback(async (result: 'pass' | 'fail', timeMs?: number) => {
        if (!currentPuzzle || !username.trim()) return;

        let timeSpent = timeMs;
        if (!timeSpent && timer.puzzleStartTime) {
            timeSpent = Date.now() - timer.puzzleStartTime;
        }

        try {
            const response = await reviewPuzzle(
                currentPuzzle.id,
                username.trim(),
                result,
                timeSpent,
                activeSessionId || undefined,
            );

            if (response.feedback) {
                setLastFeedback(response.feedback);
                setTimeout(() => setLastFeedback(''), 5000);
            }

            if (result === 'pass') {
                const newStreak = streak + 1;
                setStreak(newStreak);
                if (newStreak > bestStreak) {
                    setBestStreak(newStreak);
                }
            } else {
                setStreak(0);
            }

            setPerformanceHistory(prev => [...prev, { time: Date.now(), result }]);

            const effectiveStreak = result === 'pass' ? streak + 1 : 0;
            checkAchievements({ streak: effectiveStreak, currentPuzzleTime: timer.currentPuzzleTime });

            // Session progress (reviewedCount) and completion are advanced in
            // Puzzles.tsx when the user *moves on* from a puzzle — one step per
            // puzzle — NOT here. Counting every review would let a "mark failed &
            // try again" or a revealed solution (both of which re-review the same
            // puzzle) inflate the count and end the session before the last
            // puzzle, stranding a dead "Next Puzzle" button.
        } catch (err) {
            console.error('Failed to review puzzle:', err);
            setError(err instanceof Error ? err.message : 'Failed to review puzzle');
        }
    }, [
        activeSessionId,
        bestStreak,
        checkAchievements,
        currentPuzzle,
        timer.puzzleStartTime,
        timer.currentPuzzleTime,
        streak,
        username,
    ]);

    // ── Keep handleReviewPuzzleRef in sync (for timer timeout callback) ──
    const handleReviewPuzzleRef = useRef(handleReviewPuzzle);
    useEffect(() => {
        handleReviewPuzzleRef.current = handleReviewPuzzle;
    }, [handleReviewPuzzle]);

    // ── handleStartSession ──
    const handleStartSession = useCallback(async () => {
        if (!username.trim()) {
            setError('Please enter a username');
            return;
        }

        if (!userStatus) {
            setError('Loading user status...');
            return;
        }

        if (userStatus.puzzles_count === 0) {
            setError('No puzzles available. Generate puzzles first.');
            return;
        }

        if (userStatus.due_count === 0) {
            setError('No puzzles are due for review right now. Check back later or generate more puzzles.');
            return;
        }

        setSessionState('loading');
        setError(null);
        setLastFeedback('');

        try {
            let targetAccuracyParam: number | undefined = undefined;
            let targetTimeMinutesParam: number | undefined = undefined;

            if (sessionType === 'accuracy_goal') {
                targetAccuracyParam = targetAccuracy;
            } else if (sessionType === 'timed') {
                targetTimeMinutesParam = targetTimeMinutes;
            }

            const { session_id } = await startSession(
                username.trim(),
                5,
                sessionType,
                targetAccuracyParam,
                targetTimeMinutesParam,
                warmupMode ? { is_warmup: true } : undefined,
            );
            setActiveSessionId(session_id);

            if (sessionType === 'timed') {
                timer.startSessionTimer(targetTimeMinutes * 60, handleCompleteSession);
            }
            localStorage.setItem(`knightmind:session:${username.trim()}`, session_id);
            localStorage.removeItem(`knightmind:sessionState:${username.trim()}`);
            setSessionSummary(null);
            setReviewedCount(0);
            setStreak(0);
            setHintsUsed(0);
            setPerformanceHistory([]);

            setIsLoading(true);
            try {
                const response = await getDuePuzzles(
                    username.trim(),
                    5,
                    sessionType,
                    sessionType === 'accuracy_goal' ? targetAccuracy : undefined,
                    motifFilter || undefined,
                );
                setPuzzles(response.puzzles);
                setCurrentIndex(0);
                setStatus('solving');
                if (response.puzzles.length > 0) {
                    setSessionState('active');
                    setError(null);
                } else {
                    setSessionState('error');
                }
            } catch (puzErr) {
                const message = puzErr instanceof Error ? puzErr.message : 'Failed to load session puzzles';
                setSessionState('error');
                setError(message);
            } finally {
                setIsLoading(false);
            }
        } catch (err) {
            const message = err instanceof Error ? err.message : 'Failed to start session';
            setSessionState('error');
            setError(message);
        }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- use timer.startSessionTimer, not the unstable timer object
    }, [
        username,
        userStatus,
        sessionType,
        targetAccuracy,
        targetTimeMinutes,
        warmupMode,
        motifFilter,
        setActiveSessionId,
        setStatus,
        timer.startSessionTimer,
        handleCompleteSession,
    ]);

    // ── handleUseHint ──
    const handleUseHint = useCallback(async () => {
        if (!activeSessionId || !username.trim()) return;

        try {
            const updatedSession = await requestHint(activeSessionId, username.trim());
            setHintsUsed(updatedSession.hints_used);
            setSessionSummary(updatedSession);
        } catch (err) {
            console.error('Failed to use hint:', err);
            setError(err instanceof Error ? err.message : 'Failed to use hint');
        }
    }, [activeSessionId, username]);

    return {
        sessionState,
        sessionSummary,
        isResumingSession,
        streak,
        bestStreak,
        hintsUsed,
        reviewedCount,
        performanceHistory,
        puzzles,
        currentIndex,
        isLoading,
        error,
        lastFeedback,
        setPuzzles,
        setCurrentIndex,
        setError,
        setLastFeedback,
        setSessionSummary,
        setSessionState,
        setReviewedCount,
        setIsLoading,
        handleStartSession,
        handleCompleteSession,
        handleReviewPuzzle,
        handleUseHint,
        calculateRecentPerformance,
        getPerformanceTrend,
    };
}
