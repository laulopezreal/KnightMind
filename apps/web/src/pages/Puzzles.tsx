import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { Chessboard } from 'react-chessboard';
import { Chess } from 'chess.js';
import { generatePuzzles, getDailyPuzzles, getDuePuzzles, cancelJob, ApiError, type Puzzle, startSession, completeSession, getRecentSessions, reviewPuzzle, getSession, type SessionSummary, useHint as requestHint, getUserStatus, type UserStatus, getMotifPerformance, type MotifPerformanceResponse } from '../api';
import { JobStatusCard } from '../components/JobStatusCard';
import { useJobPolling } from '../hooks/useJobPolling';
import { useChessUsername } from '../context/ChessUsernameContext';
import { parseBestMoveUci, getPieceNameAtSquare } from '../utils/puzzle-clue';

type PuzzleStatus = 'solving' | 'correct' | 'incorrect' | 'revealed';
type ClueStage = 0 | 1 | 2;
type SessionState = 'idle' | 'loading' | 'active' | 'completing' | 'completed' | 'error';
type SessionType = 'standard' | 'timed' | 'accuracy_goal';

const calculateAccuracy = (passCount: number, failCount: number): number => {
    const total = passCount + failCount;
    return total > 0 ? Math.round((passCount / total) * 100) : 0;
};

// Achievement types
interface Achievement {
    id: string;
    name: string;
    description: string;
    icon: string;
    earned: boolean;
    earnedAt?: Date;
}

// Define achievements
const ACHIEVEMENTS: Achievement[] = [
    {
        id: 'first_session',
        name: 'First Steps',
        description: 'Complete your first training session',
        icon: '👣',
        earned: false
    },
    {
        id: 'streak_5',
        name: 'Hot Streak',
        description: 'Achieve a 5 puzzle streak',
        icon: '🔥',
        earned: false
    },
    {
        id: 'streak_10',
        name: 'Blazing Streak',
        description: 'Achieve a 10 puzzle streak',
        icon: '🧨',
        earned: false
    },
    {
        id: 'accuracy_90',
        name: 'Sharp Shooter',
        description: 'Achieve 90% accuracy in a session',
        icon: '🎯',
        earned: false
    },
    {
        id: 'speed_demon',
        name: 'Speed Demon',
        description: 'Solve a puzzle in under 10 seconds',
        icon: '⚡',
        earned: false
    },
    {
        id: 'perfect_session',
        name: 'Flawless Victory',
        description: 'Complete a session with 100% accuracy',
        icon: '🏆',
        earned: false
    }
];

export default function Puzzles() {
    const { username, setEditorOpen } = useChessUsername();
    const [puzzles, setPuzzles] = useState<Puzzle[]>([]);
    const [currentIndex, setCurrentIndex] = useState(0);
    const [userMove, setUserMove] = useState('');
    const [status, setStatus] = useState<PuzzleStatus>('solving');
    const [isLoading, setIsLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [showUciInput, setShowUciInput] = useState(false);
    const [activeJobId, setActiveJobId] = useState<string | null>(null);

    // Session state
    const [activeSessionId, setActiveSessionId] = useState<string | null>(null);
    const [sessionSummary, setSessionSummary] = useState<SessionSummary | null>(null);
    const [recentSessions, setRecentSessions] = useState<SessionSummary[]>([]);
    const [reviewedCount, setReviewedCount] = useState(0);
    const [isResumingSession, setIsResumingSession] = useState(false);
    const [sessionState, setSessionState] = useState<SessionState>('idle');
    const [clueStage, setClueStage] = useState<ClueStage>(0);

    // Enhanced session features
    const [sessionType, setSessionType] = useState<SessionType>('standard');
    const [targetAccuracy, setTargetAccuracy] = useState<number>(80);
    const [targetTimeMinutes, setTargetTimeMinutes] = useState<number>(10);
    const [puzzleStartTime, setPuzzleStartTime] = useState<number | null>(null);
    const [streak, setStreak] = useState(0);
    const [bestStreak, setBestStreak] = useState(0);
    const [hintsUsed, setHintsUsed] = useState(0);

    // Performance tracking
    const [performanceHistory, setPerformanceHistory] = useState<Array<{ time: number, result: 'pass' | 'fail' }>>([]);
    const [currentPuzzleTime, setCurrentPuzzleTime] = useState<number>(0);

    // Achievements
    const [achievements, setAchievements] = useState<Achievement[]>(ACHIEVEMENTS);

    // Timer for timed sessions
    const puzzleTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
    const sessionTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
    const [timeRemaining, setTimeRemaining] = useState<number>(0);
    const puzzleTimeRef = useRef<ReturnType<typeof setInterval> | null>(null);
    const [userStatus, setUserStatus] = useState<UserStatus | null>(null);
    const [isLoadingStatus, setIsLoadingStatus] = useState(false);
    const [motifPerformance, setMotifPerformance] = useState<MotifPerformanceResponse | null>(null);

    const statusRef = useRef(status);
    statusRef.current = status;

    const handleReviewPuzzleRef = useRef<((result: 'pass' | 'fail', timeMs?: number) => Promise<void>)>(async () => { });

    // Mock progress for now until we hook up real polling
    // const mockProgress = 0; 


    const currentPuzzle = puzzles[currentIndex];
    const puzzlesAvailable = puzzles.length > 0;
    const isFinalPuzzle = puzzlesAvailable && currentIndex >= puzzles.length - 1;
    const finishButtonDisabled = isFinalPuzzle ? sessionState !== 'active' : false;
    const controlsEnabled = sessionState === 'idle' || sessionState === 'error';

    // Load persisted job and session from local storage on mount or username change
    useEffect(() => {
        if (!username) return;
        const savedJobId = localStorage.getItem(`knightmind:lastJob:${username}`);
        if (savedJobId) {
            setActiveJobId(savedJobId);
        } else {
            setActiveJobId(null);
        }

        const savedSessionId = localStorage.getItem(`knightmind:session:${username}`);

        const loadSessionAndPuzzles = async () => {
            if (!savedSessionId) return;

            try {
                setIsResumingSession(true);
                const session = await getSession(savedSessionId);

                if (session.completed_at) {
                    // Session already completed, clear it
                    localStorage.removeItem(`knightmind:session:${username}`);
                    setActiveSessionId(null);
                    return;
                }

                // Session valid and active
                setActiveSessionId(session.session_id);
                setSessionSummary(session);
                setReviewedCount(session.pass_count + session.fail_count);

                setSessionState('loading');
                setError(null);
                setIsLoading(true);
                try {
                    const response = await getDuePuzzles(username, session.requested_n);
                    setPuzzles(response.puzzles);
                    setCurrentIndex(0);
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
                    setSessionState(puzzles.length > 0 ? 'active' : 'error');
                } finally {
                    setIsLoading(false);
                }
            } catch (err) {
                // Session not found or error, clear it
                console.error("Failed to resume session:", err);
                localStorage.removeItem(`knightmind:session:${username}`);
                setActiveSessionId(null);
                setSessionState('idle');
            } finally {
                setIsResumingSession(false);
            }
        };

        const loadRecent = async () => {
            try {
                const sessions = await getRecentSessions(username, 5);
                setRecentSessions(sessions);
            } catch (err) {
                setError(err instanceof Error ? err.message : 'Failed to load recent sessions');
            }
        };

        loadSessionAndPuzzles();
        loadRecent();
        // eslint-disable-next-line react-hooks/exhaustive-deps -- run only on username change
    }, [username]);

    useEffect(() => {
        if (!username) {
            setUserStatus(null);
            return;
        }

        let cancelled = false;

        const fetchStatus = async () => {
            setIsLoadingStatus(true);
            try {
                const status = await getUserStatus(username);
                if (!cancelled) {
                    setUserStatus(status);
                }
            } catch (err) {
                if (!cancelled) {
                    console.warn('Unable to load user status:', err);
                    setUserStatus(null);
                }
            } finally {
                if (!cancelled) {
                    setIsLoadingStatus(false);
                }
            }
        };

        fetchStatus();

        return () => {
            cancelled = true;
        };
    }, [username]);

    useEffect(() => {
        if (!username) {
            setMotifPerformance(null);
            return;
        }

        let cancelled = false;

        const fetchMotifs = async () => {
            try {
                const performance = await getMotifPerformance(username);
                if (!cancelled) {
                    setMotifPerformance(performance);
                }
            } catch (err) {
                if (!cancelled) {
                    console.warn('Unable to load motif performance:', err);
                    setMotifPerformance(null);
                }
            }
        };

        fetchMotifs();

        return () => {
            cancelled = true;
        };
    }, [username]);

    const { job, isPolling: isJobPolling } = useJobPolling(activeJobId, {
        enabled: !!activeJobId,
        onSuccess: async () => {
            // Clear local storage on success so we don't start polling old finished jobs next time?
            // Or keep it to show "Success" state persistently until user generates new?
            // Prompt says: "If succeeded/failed, show final state and clear stored job_id (optional)"
            // Let's keep it to show the success card, but maybe trigger auto-refresh.

            // Auto-refresh puzzles
            try {
                const res = await getDailyPuzzles(username, 5);
                setPuzzles(res.puzzles);
                setCurrentIndex(0);
                setStatus('solving');
                setUserMove('');
                if (res.puzzles.length > 0) {
                    setSessionState('active');
                    setError(null);
                } else {
                    setSessionState('error');
                    setError('No puzzles returned from generation');
                }
            } catch (err) {
                console.error('Failed to refresh puzzles after generation:', err);
                const message = err instanceof Error ? err.message : 'Failed to refresh puzzles';
                setError(message);
            }

            // Clear job ID after a delay or let user clear it?
            // If we clear it immediately, the card disappears. We probably want the card to stay "Success".
            // We can clear localStorage but keep activeJobId in state for this session.
            localStorage.removeItem(`knightmind:lastJob:${username}`);
        },
        onError: (err) => {
            // Similarly clear storage on hard failure so we don't get stuck
            localStorage.removeItem(`knightmind:lastJob:${username}`);
            const message = err instanceof Error ? err.message : 'Failed to generate puzzles';
            if (puzzles.length > 0) {
                setSessionState('active');
                setError(null);
            } else {
                setSessionState('error');
                setError(message);
            }
        }
    });

    const isGenerating = isJobPolling || (job?.status === 'queued' || job?.status === 'running');
    const controlsDisabled = !controlsEnabled || isLoading || isGenerating;

    // Sync job status to local isGenerating for backwards compat with other UI if needed, 
    // but better to rely on 'job' object.

    // Initialize achievements from localStorage
    useEffect(() => {
        if (username) {
            const savedAchievements = localStorage.getItem(`knightmind:achievements:${username}`);
            if (savedAchievements) {
                try {
                    const parsed = JSON.parse(savedAchievements);
                    // Merge with default achievements to ensure all are present
                    const merged = ACHIEVEMENTS.map(defaultAchievement => {
                        const saved = parsed.find((a: Achievement) => a.id === defaultAchievement.id);
                        if (saved) {
                            // Convert earnedAt string back to Date object
                            return {
                                ...defaultAchievement,
                                ...saved,
                                earnedAt: saved.earnedAt ? new Date(saved.earnedAt) : undefined
                            };
                        }
                        return defaultAchievement;
                    });
                    setAchievements(merged);
                } catch (e) {
                    console.error('Failed to parse saved achievements', e);
                }
            }
        }
    }, [username]);

    // Save achievements to localStorage when they change
    useEffect(() => {
        if (username && achievements.some(a => a.earned)) {
            localStorage.setItem(`knightmind:achievements:${username}`, JSON.stringify(achievements));
        }
    }, [achievements, username]);

    // Cleanup timers on unmount
    useEffect(() => {
        return () => {
            if (puzzleTimerRef.current) {
                clearTimeout(puzzleTimerRef.current);
            }
            if (sessionTimerRef.current) {
                clearInterval(sessionTimerRef.current);
            }
            if (puzzleTimeRef.current) {
                clearInterval(puzzleTimeRef.current);
            }
        };
    }, []);

    // ... (keep logic same as original, just updating UI)
    // ... (keep logic same as original, just updating UI)
    const handleGeneratePuzzles = async () => {
        if (!username.trim()) {
            setError('Please enter a username');
            return;
        }
        setSessionState('loading');
        setError(null);

        try {
            const { job_id } = await generatePuzzles(username.trim());
            setActiveJobId(job_id);
            localStorage.setItem(`knightmind:lastJob:${username.trim()}`, job_id);
            // Polling will auto-start
        } catch (err) {
            if (err instanceof ApiError) {
                if (err.statusCode === 404) {
                    setError('No games found. Please import games first.');
                } else {
                    setError(err.detail || err.message);
                }
            } else {
                setError(err instanceof Error ? err.message : 'Failed to generate puzzles');
            }
            if (puzzles.length > 0) {
                setSessionState('active');
                setError(null);
            } else {
                setSessionState('error');
            }
        }
    };

    const handleLoadPuzzles = async () => {
        if (!username.trim()) {
            setError('Please enter a username');
            return;
        }
        // Check if we already have a running job? Maybe not needed.
        setSessionState('loading');
        setIsLoading(true);
        setError(null);

        try {
            const dailyPuzzles = await getDailyPuzzles(username.trim(), 5);
            setPuzzles(dailyPuzzles.puzzles);
            setCurrentIndex(0);
            setStatus('solving');
            setUserMove('');
            setError(null);
            setReviewedCount(0); // Reset reviewed count
            if (dailyPuzzles.puzzles.length > 0) {
                setSessionState('active');
            } else {
                setSessionState('error');
            }
        } catch (err) {
            if (err instanceof ApiError) {
                if (err.statusCode === 404) {
                    setError('No puzzles found. Generate puzzles first.');
                } else {
                    setError(err.detail || err.message);
                }
            } else {
                setError(err instanceof Error ? err.message : 'Failed to load puzzles');
            }
            if (puzzles.length > 0) {
                setSessionState('active');
                setError(null);
            } else {
                setSessionState('error');
            }
        } finally {
            setIsLoading(false);
        }
    };

    const handleCancelJob = async () => {
        if (!activeJobId) return;

        try {
            await cancelJob(activeJobId);
            // Job status will be updated via polling
        } catch (err) {
            console.error('Failed to cancel job:', err);
            setError(err instanceof Error ? err.message : 'Failed to cancel job');
        }
    };

    // Session handlers
    const handleStartSession = async () => {
        if (!username.trim()) {
            setError('Please enter a username');
            return;
        }

        setSessionState('loading');
        setError(null);
        setLastFeedback('');

        try {
            // Determine parameters based on session type
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
                targetTimeMinutesParam
            );
            setActiveSessionId(session_id);

            // Set up session timer AFTER setting session ID so closure captures the correct value
            if (sessionType === 'timed') {
                setTimeRemaining(targetTimeMinutes * 60); // Convert to seconds
                if (sessionTimerRef.current) clearInterval(sessionTimerRef.current);
                sessionTimerRef.current = setInterval(() => {
                    setTimeRemaining(prev => {
                        if (prev <= 1) {
                            if (sessionTimerRef.current) clearInterval(sessionTimerRef.current);
                            // Auto-complete session when time runs out
                            // Use session_id directly instead of activeSessionId to avoid stale closure
                            handleCompleteSession();
                            return 0;
                        }
                        return prev - 1;
                    });
                }, 1000);
            }
            localStorage.setItem(`knightmind:session:${username.trim()}`, session_id);
            setSessionSummary(null);
            setReviewedCount(0);
            setStreak(0);
            setBestStreak(0);
            setHintsUsed(0);

            // Load puzzles
            // FIX: Use getDuePuzzles for session training
            setIsLoading(true);
            try {
                const response = await getDuePuzzles(
                    username.trim(),
                    5,
                    sessionType,
                    sessionType === 'accuracy_goal' ? targetAccuracy : undefined
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
                if (puzzles.length > 0) {
                    setSessionState('active');
                    setError(null);
                } else {
                    setSessionState('error');
                    setError(message);
                }
            } finally {
                setIsLoading(false);
            }
        } catch (err) {
            const message = err instanceof Error ? err.message : 'Failed to start session';
            if (puzzles.length > 0) {
                setSessionState('active');
                setError(null);
            } else {
                setSessionState('error');
                setError(message);
            }
        }
    };

    // Helper function to check and award achievements
    const checkAchievements = useCallback((newAchievements: Achievement[] = achievements) => {
        const updatedAchievements = [...newAchievements];
        let achievementsChanged = false;

        // Check for streak achievements
        if (streak >= 5 && !updatedAchievements.find(a => a.id === 'streak_5')?.earned) {
            const achievement = updatedAchievements.find(a => a.id === 'streak_5');
            if (achievement) {
                achievement.earned = true;
                achievement.earnedAt = new Date();
                achievementsChanged = true;
            }
        }

        if (streak >= 10 && !updatedAchievements.find(a => a.id === 'streak_10')?.earned) {
            const achievement = updatedAchievements.find(a => a.id === 'streak_10');
            if (achievement) {
                achievement.earned = true;
                achievement.earnedAt = new Date();
                achievementsChanged = true;
            }
        }

        // Check for speed achievement
        if (currentPuzzleTime < 10 && !updatedAchievements.find(a => a.id === 'speed_demon')?.earned) {
            const achievement = updatedAchievements.find(a => a.id === 'speed_demon');
            if (achievement) {
                achievement.earned = true;
                achievement.earnedAt = new Date();
                achievementsChanged = true;
            }
        }

        if (achievementsChanged) {
            setAchievements(updatedAchievements);
        }

        return updatedAchievements;
    }, [achievements, currentPuzzleTime, streak]);

    // Helper function to calculate accuracy percentage
    // const calculateAccuracy = (passCount: number, failCount: number): number => {
    //     const total = passCount + failCount;
    //     return total > 0 ? Math.round((passCount / total) * 100) : 0;
    // };

    // Helper function to check session completion achievements
    const checkSessionAchievements = useCallback(() => {
        const updatedAchievements = [...achievements];
        let achievementsChanged = false;

        // First session achievement (if this is the first session)
        if (!updatedAchievements.find(a => a.id === 'first_session')?.earned) {
            const achievement = updatedAchievements.find(a => a.id === 'first_session');
            if (achievement) {
                achievement.earned = true;
                achievement.earnedAt = new Date();
                achievementsChanged = true;
            }
        }

        // Accuracy achievements
        if (sessionSummary && sessionSummary.pass_count + sessionSummary.fail_count > 0) {
            const accuracy = calculateAccuracy(sessionSummary.pass_count, sessionSummary.fail_count);

            if (accuracy >= 90 && !updatedAchievements.find(a => a.id === 'accuracy_90')?.earned) {
                const achievement = updatedAchievements.find(a => a.id === 'accuracy_90');
                if (achievement) {
                    achievement.earned = true;
                    achievement.earnedAt = new Date();
                    achievementsChanged = true;
                }
            }

            if (accuracy === 100 && !updatedAchievements.find(a => a.id === 'perfect_session')?.earned) {
                const achievement = updatedAchievements.find(a => a.id === 'perfect_session');
                if (achievement) {
                    achievement.earned = true;
                    achievement.earnedAt = new Date();
                    achievementsChanged = true;
                }
            }
        }

        if (achievementsChanged) {
            setAchievements(updatedAchievements);
        }

        return updatedAchievements;
    }, [achievements, sessionSummary]);

    const handleCompleteSession = useCallback(async () => {
        if (!activeSessionId || !username.trim()) return;

        setSessionState('completing');
        setError(null); // Clear any previous errors

        // Clean up timers
        if (puzzleTimerRef.current) {
            clearTimeout(puzzleTimerRef.current);
            puzzleTimerRef.current = null;
        }
        if (sessionTimerRef.current) {
            clearInterval(sessionTimerRef.current);
            sessionTimerRef.current = null;
        }

        try {
            const summary = await completeSession(activeSessionId, username.trim());
            setSessionSummary(summary);
            setActiveSessionId(null);
            localStorage.removeItem(`knightmind:session:${username.trim()}`);
            setSessionState('completed');

            // Check for session completion achievements
            const updatedAchievements = checkSessionAchievements();

            // Save achievements to localStorage
            if (updatedAchievements.some(a => a.earned)) {
                localStorage.setItem(`knightmind:achievements:${username.trim()}`, JSON.stringify(updatedAchievements));
            }

            // Refresh recent sessions
            const recent = await getRecentSessions(username.trim(), 5);
            setRecentSessions(recent);

            // Refresh motif performance
            try {
                const updated = await getMotifPerformance(username.trim());
                setMotifPerformance(updated);
            } catch (motifErr) {
                console.warn('Failed to refresh motif performance:', motifErr);
            }
        } catch (err) {
            console.error('Failed to complete session:', err);
            const errorMessage = err instanceof Error ? err.message : 'Failed to complete session. Please try again.';
            setError(errorMessage);
            setSessionState('active');
        }
    }, [activeSessionId, checkSessionAchievements, username]);

    const handleReviewPuzzle = useCallback(async (result: 'pass' | 'fail', timeMs?: number) => {
        if (!currentPuzzle || !username.trim()) return;

        // Calculate time spent on this puzzle if not provided
        let timeSpent = timeMs;
        if (!timeSpent && puzzleStartTime) {
            timeSpent = Date.now() - puzzleStartTime;
        }

        try {
            const response = await reviewPuzzle(
                currentPuzzle.id,
                username.trim(),
                result,
                timeSpent,
                activeSessionId || undefined
            );

            // Set feedback message
            if (response.feedback) {
                setLastFeedback(response.feedback);
                // Clear feedback after a few seconds
                setTimeout(() => setLastFeedback(''), 5000);
            }

            // Update streak
            if (result === 'pass') {
                const newStreak = streak + 1;
                setStreak(newStreak);
                if (newStreak > bestStreak) {
                    setBestStreak(newStreak);
                }
            } else {
                setStreak(0);
            }

            // Update performance history
            setPerformanceHistory(prev => [...prev, { time: Date.now(), result }]);

            // Check for achievements
            checkAchievements();

            // Increment reviewed count
            const newCount = reviewedCount + 1;
            setReviewedCount(newCount);

            // Check if session is complete
            if (activeSessionId && newCount >= puzzles.length) {
                await handleCompleteSession();
            }
        } catch (err) {
            console.error('Failed to review puzzle:', err);
            setError(err instanceof Error ? err.message : 'Failed to review puzzle');
        }
    }, [
        activeSessionId,
        bestStreak,
        checkAchievements,
        currentPuzzle,
        handleCompleteSession,
        puzzleStartTime,
        puzzles.length,
        reviewedCount,
        streak,
        username,
    ]);

    // Keep ref in sync
    useEffect(() => {
        handleReviewPuzzleRef.current = handleReviewPuzzle;
    }, [handleReviewPuzzle]);


    const shouldShowJobStatusCard =
        !!job &&
        (job.status === 'queued' ||
            job.status === 'running' ||
            (!puzzlesAvailable && (job.status === 'succeeded' || job.status === 'failed')));
    const shouldShowErrorCard = sessionState === 'error' && !!error;
    const shouldShowEmptyState =
        !isLoading &&
        !isGenerating &&
        !puzzlesAvailable &&
        !shouldShowJobStatusCard &&
        !error;


    const handleCheckAnswer = () => {
        if (!currentPuzzle) return;
        const normalizedUserMove = userMove.trim().toLowerCase();
        const normalizedBestMove = currentPuzzle.best_move_uci.toLowerCase();
        if (normalizedUserMove === normalizedBestMove) setStatus('correct');
        else setStatus('incorrect');
    };

    const handleRevealSolution = () => {
        setStatus('revealed');
        const bestMove = currentPuzzle?.best_move_uci?.toLowerCase();
        setUserMove(bestMove || '');
        if (currentPuzzle && bestMove) {
            const solutionGame = new Chess(currentPuzzle.fen);
            const from = bestMove.slice(0, 2);
            const to = bestMove.slice(2, 4);
            const promotion = bestMove.slice(4, 5);
            solutionGame.move({ from, to, promotion: promotion || undefined });
            setGame(solutionGame);
        }
    };

    const handleUseHint = async () => {
        if (!activeSessionId || !username.trim()) return;

        try {
            const updatedSession = await requestHint(activeSessionId, username.trim());
            setHintsUsed(updatedSession.hints_used);
            setSessionSummary(updatedSession);
        } catch (err) {
            console.error('Failed to use hint:', err);
            setError(err instanceof Error ? err.message : 'Failed to use hint');
        }
    };

    const handleClue = () => {
        if (!currentPuzzle?.best_move_uci) return;
        if (clueStage === 0) {
            setClueStage(1);
        } else if (clueStage === 1) {
            setClueStage(2);
            handleRevealSolution();
        }
    };

    const [game, setGame] = useState(new Chess());
    const [lastFeedback, setLastFeedback] = useState<string>('');

    const bestMoveParsed = useMemo(() => {
        if (!currentPuzzle?.best_move_uci) return { from: '', to: '' };
        return parseBestMoveUci(currentPuzzle.best_move_uci);
    }, [currentPuzzle?.best_move_uci]);

    const clueSquareStyles: Record<string, { backgroundColor: string }> =
        currentPuzzle?.best_move_uci && clueStage >= 1
            ? (() => {
                const { from, to } = bestMoveParsed;
                const highlight = { backgroundColor: 'rgba(255, 235, 59, 0.45)' };
                if (clueStage === 2 && to) return { [from]: highlight, [to]: highlight };
                return from ? { [from]: highlight } : {};
            })()
            : {};

    useEffect(() => {
        if (currentPuzzle) {
            setGame(new Chess(currentPuzzle.fen));
            setClueStage(0);
            // Start timer for this puzzle
            const startTime = Date.now();
            setPuzzleStartTime(startTime);
            setCurrentPuzzleTime(0);

            // Set up timer for timed sessions
            // Use sessionType state instead of sessionSummary since sessionSummary is null for new sessions
            if (sessionType === 'timed' && activeSessionId) {
                if (puzzleTimerRef.current) clearTimeout(puzzleTimerRef.current);
                puzzleTimerRef.current = setTimeout(() => {
                    // Auto-mark as failed if time runs out
                    if (statusRef.current === 'solving') {
                        handleReviewPuzzleRef.current('fail');
                        setStatus('incorrect');
                    }
                }, 30000); // 30 seconds per puzzle in timed mode
            }

            // Set up puzzle time tracker
            if (puzzleTimeRef.current) clearInterval(puzzleTimeRef.current);
            puzzleTimeRef.current = setInterval(() => {
                setCurrentPuzzleTime(Math.floor((Date.now() - startTime) / 1000));
            }, 1000);
        }

        return () => {
            if (puzzleTimerRef.current) {
                clearTimeout(puzzleTimerRef.current);
                puzzleTimerRef.current = null;
            }
            if (puzzleTimeRef.current) {
                clearInterval(puzzleTimeRef.current);
                puzzleTimeRef.current = null;
            }
        };
    }, [currentPuzzle, sessionType, activeSessionId]);

    const onPieceDrop = (sourceSquare: string, targetSquare: string) => {
        if (!currentPuzzle || status === 'correct' || status === 'revealed') return false;
        try {
            const move = game.move({ from: sourceSquare, to: targetSquare, promotion: 'q' });
            if (move === null) return false;
            setClueStage(0);
            setGame(new Chess(game.fen()));
            const uciMove = `${move.from}${move.to}${move.promotion || ''}`;
            setUserMove(uciMove);
            const normalizedBestMove = currentPuzzle.best_move_uci.toLowerCase();
            if (uciMove === normalizedBestMove) setStatus('correct');
            else setStatus('incorrect');
            return true;
        } catch { return false; }
    };

    const handleNextPuzzle = () => {
        if (currentIndex < puzzles.length - 1) {
            setCurrentIndex(currentIndex + 1);
            setStatus('solving');
            setUserMove('');
            setClueStage(0);
        }
    };

    const handleAdvancePuzzle = async () => {
        if (status === 'correct') {
            await handleReviewPuzzle('pass');
        }

        if (!isFinalPuzzle) {
            handleNextPuzzle();
        }
    };


    // Helper function to calculate recent performance
    const calculateRecentPerformance = (history: Array<{ time: number, result: 'pass' | 'fail' }>, minutes: number = 5): number => {
        const cutoffTime = Date.now() - (minutes * 60 * 1000);
        const recent = history.filter(item => item.time > cutoffTime);
        if (recent.length === 0) return 0;
        const passCount = recent.filter(item => item.result === 'pass').length;
        return Math.round((passCount / recent.length) * 100);
    };

    // Helper function to get performance trend
    const getPerformanceTrend = (history: Array<{ time: number, result: 'pass' | 'fail' }>): 'improving' | 'declining' | 'stable' => {
        if (history.length < 4) return 'stable';

        const recent = history.slice(-4);
        const firstHalf = recent.slice(0, 2);
        const secondHalf = recent.slice(2, 4);

        const firstHalfAccuracy = firstHalf.filter(item => item.result === 'pass').length / firstHalf.length;
        const secondHalfAccuracy = secondHalf.filter(item => item.result === 'pass').length / secondHalf.length;

        if (secondHalfAccuracy > firstHalfAccuracy + 0.1) return 'improving';
        if (secondHalfAccuracy < firstHalfAccuracy - 0.1) return 'declining';
        return 'stable';
    };

    return (
        <div className="space-y-12 animate-teedin">
            <section>
                <Link to="/" className="text-primary/40 hover:text-primary mb-4 inline-block font-sans text-sm tracking-widest uppercase transition-colors">
                    ← Return Home
                </Link>
                <div className="flex justify-between items-end">
                    <div>
                        <h1 className="text-4xl md:text-5xl font-serif text-primary mb-2">Daily Puzzles</h1>
                        <p className="text-lg text-primary/60 font-sans">Tactical patterns from your own games.</p>
                    </div>
                </div>
            </section>

            {/* Controls */}
            <section className="bg-primary/5 border border-primary/10 rounded-sm p-6 backdrop-blur-sm">
                <div className="flex flex-col md:flex-row gap-6 relative items-end">
                    <div className="flex-1 relative min-w-[300px]">
                        {!username ? (
                            <div className="h-full flex items-center">
                                <span className="text-primary/60 font-sans mr-2">Set your Chess.com username to continue.</span>
                                <button
                                    type="button"
                                    onClick={() => setEditorOpen(true)}
                                    className="km-interactive km-focus-visible km-inline-link text-primary"
                                >
                                    Set Username
                                </button>
                            </div>
                        ) : (
                            <div className="text-xl font-serif text-primary py-2 border-b border-primary/20">
                                {username}
                            </div>
                        )}
                    </div>
                    <div className="flex gap-4">
                        {!activeSessionId && (
                            <>
                                <select
                                    value={sessionType}
                                    onChange={(e) => setSessionType(e.target.value as SessionType)}
                                    className="px-3 py-2 border border-primary/20 rounded-sm bg-bg-primary text-primary"
                                >
                                    <option value="standard">Standard</option>
                                    <option value="timed">Timed</option>
                                    <option value="accuracy_goal">Accuracy Goal</option>
                                </select>
                                {sessionType === 'accuracy_goal' && (
                                    <input
                                        type="number"
                                        min="50"
                                        max="100"
                                        value={targetAccuracy}
                                        onChange={(e) => setTargetAccuracy(Number(e.target.value))}
                                        className="px-3 py-2 border border-primary/20 rounded-sm bg-bg-primary text-primary w-24"
                                        placeholder="Accuracy %"
                                    />
                                )}
                                {sessionType === 'timed' && (
                                    <input
                                        type="number"
                                        min="1"
                                        max="60"
                                        value={targetTimeMinutes}
                                        onChange={(e) => setTargetTimeMinutes(Number(e.target.value))}
                                        className="px-3 py-2 border border-primary/20 rounded-sm bg-bg-primary text-primary w-24"
                                        placeholder="Minutes"
                                    />
                                )}
                                <button type="button" onClick={handleStartSession} disabled={controlsDisabled}
                                    className={`px-6 py-2 bg-accent text-bg-primary rounded-sm font-serif transition-colors km-focus-visible ${controlsDisabled ? 'km-interactive-disabled disabled:opacity-50' : 'km-interactive'}`}>
                                    Start Session
                                </button>
                            </>
                        )}
                        <button type="button" onClick={handleLoadPuzzles} disabled={controlsDisabled}
                            className={`px-6 py-2 border border-primary/20 text-primary rounded-sm font-serif transition-all km-focus-visible ${controlsDisabled ? 'km-interactive-disabled disabled:opacity-50' : 'km-interactive'}`}>
                            {isLoading ? 'Loading...' : 'Load Puzzles'}
                        </button>
                        <button type="button" onClick={handleGeneratePuzzles} disabled={controlsDisabled}
                            className={`px-6 py-2 bg-primary text-bg-primary rounded-sm font-serif transition-colors km-focus-visible ${controlsDisabled ? 'km-interactive-disabled disabled:opacity-50' : 'km-interactive'}`}>
                            {isGenerating ? 'Generating...' : 'Generate New'}
                        </button>
                    </div>
                </div>

                {/* Job Status / Error Area */}
                <div className="mt-6">
                    {shouldShowEmptyState && userStatus && (
                        <div className="bg-primary/5 border border-primary/10 rounded-sm p-6 backdrop-blur-sm text-center space-y-4">
                            {userStatus.games_count === 0 ? (
                                <>
                                    <h3 className="font-serif text-xl text-primary">No games imported yet</h3>
                                    <p className="text-primary/60 font-sans">
                                        Import your Chess.com games to generate personalized puzzles.
                                    </p>
                                    <Link
                                        to="/"
                                        className="inline-block px-6 py-2 bg-primary text-bg-primary rounded-sm font-serif transition-colors km-interactive km-focus-visible"
                                    >
                                        Go to Home
                                    </Link>
                                </>
                            ) : userStatus.puzzles_count === 0 ? (
                                <>
                                    <h3 className="font-serif text-xl text-primary">Ready to generate puzzles</h3>
                                    <p className="text-primary/60 font-sans">
                                        We found {userStatus.games_count} games. Let&apos;s create training puzzles.
                                    </p>
                                    <button
                                        type="button"
                                        onClick={handleGeneratePuzzles}
                                        disabled={controlsDisabled}
                                        className={`px-6 py-2 bg-primary text-bg-primary rounded-sm font-serif transition-colors km-focus-visible ${controlsDisabled ? 'km-interactive-disabled disabled:opacity-50' : 'km-interactive'}`}
                                    >
                                        Generate Puzzles
                                    </button>
                                </>
                            ) : userStatus.due_count === 0 ? (
                                <>
                                    <h3 className="font-serif text-xl text-primary">All caught up</h3>
                                    <p className="text-primary/60 font-sans">
                                        {userStatus.next_due_at
                                            ? `Next review on ${new Date(userStatus.next_due_at).toLocaleDateString(undefined, { weekday: 'long', month: 'short', day: 'numeric' })}.`
                                            : 'No puzzles are due for review yet.'}
                                    </p>
                                    {userStatus.has_new_games && (
                                        <button
                                            type="button"
                                            onClick={handleGeneratePuzzles}
                                            disabled={controlsDisabled}
                                            className={`px-6 py-2 bg-primary text-bg-primary rounded-sm font-serif transition-colors km-focus-visible ${controlsDisabled ? 'km-interactive-disabled disabled:opacity-50' : 'km-interactive'}`}
                                        >
                                            Generate from New Games
                                        </button>
                                    )}
                                </>
                            ) : (
                                <>
                                    <h3 className="font-serif text-xl text-primary">
                                        {userStatus.due_count} puzzle{userStatus.due_count === 1 ? '' : 's'} ready
                                    </h3>
                                    <p className="text-primary/60 font-sans">
                                        Start a session to review your due puzzles.
                                    </p>
                                </>
                            )}
                        </div>
                    )}
                    {shouldShowEmptyState && isLoadingStatus && (
                        <div className="text-center text-primary/40 py-4">
                            <span className="animate-pulse">Loading training status...</span>
                        </div>
                    )}
                    {shouldShowEmptyState && !userStatus && !isLoadingStatus && (
                        <div className="bg-primary/5 border border-primary/10 rounded-sm p-6 backdrop-blur-sm text-center space-y-4">
                            <h3 className="font-serif text-xl text-primary">Ready to train</h3>
                            <p className="text-primary/60 font-sans">
                                Click &quot;Load Puzzles&quot; to start a training session, or &quot;Generate New&quot; to create fresh puzzles from your games.
                            </p>
                        </div>
                    )}
                    {shouldShowErrorCard && (
                        <JobStatusCard status="failed" error={error ?? 'Failed to generate puzzles'} />
                    )}
                    {shouldShowJobStatusCard && job && (
                        <JobStatusCard
                            status={job.status}
                            message={job.message}
                            progress={job.progress}
                            error={job.status === 'failed' ? job.message : undefined}
                            onCancel={handleCancelJob}
                        />
                    )}
                    {isLoading && !isGenerating && (
                        <JobStatusCard status="running" message="Loading puzzles..." />
                    )}
                </div>
            </section>

            {/* Weak Areas Card */}
            {motifPerformance && motifPerformance.weakest_motifs.length > 0 && (
                <section className="bg-primary/5 border border-primary/10 rounded-sm p-6 backdrop-blur-sm">
                    <h3 className="text-lg font-serif text-primary mb-4">
                        Your Weak Areas
                    </h3>
                    <div className="space-y-2">
                        {motifPerformance.motifs
                            .filter(m => m.rank === 'needs_work')
                            .map(motif => (
                                <div key={motif.name} className="flex justify-between items-center p-3 bg-red-500/10 rounded-sm">
                                    <div>
                                        <span className="font-serif text-primary">{motif.name}</span>
                                        <span className="text-xs text-primary/60 ml-2">
                                            {motif.passed}/{motif.total_puzzles} correct
                                        </span>
                                    </div>
                                    <div className="flex items-center gap-2">
                                        <span className="text-red-500 font-mono text-sm">
                                            {Math.round(motif.accuracy * 100)}%
                                        </span>
                                        <span className="text-xs text-primary/40">needs work</span>
                                    </div>
                                </div>
                            ))}
                    </div>
                </section>
            )}

            {currentPuzzle && ( // Make sure currentPuzzle is defined or access checked
                <section className="grid lg:grid-cols-2 gap-12 lg:gap-24">
                    {/* Chessboard */}
                    <div className="order-2 lg:order-1">
                        <div className="aspect-square w-full max-w-[600px] mx-auto shadow-2xl shadow-primary/5 rounded-sm overflow-hidden border border-primary/10">
                            <Chessboard
                                options={{
                                    position: game.fen(),
                                    onPieceDrop: ({ sourceSquare, targetSquare }) => targetSquare ? onPieceDrop(sourceSquare, targetSquare) : false,
                                    boardOrientation: currentPuzzle.side_to_move === 'white' ? 'white' : 'black',
                                    darkSquareStyle: { backgroundColor: 'var(--color-chess-brown-700)' },
                                    lightSquareStyle: { backgroundColor: 'var(--color-chess-cream-300)' },
                                    squareStyles: clueSquareStyles,
                                }}
                            />
                        </div>
                    </div>

                    {/* Sidebar Controls */}
                    <div className="order-1 lg:order-2 space-y-8 flex flex-col justify-center">
                        <div className="space-y-2">
                            <div className="flex justify-between items-center bg-primary/5 p-4 rounded-sm border-l-2 border-primary">
                                <div className="flex flex-col w-full">
                                    {activeSessionId && sessionSummary && (
                                        <div className="bg-primary/5 border border-primary/20 rounded-lg p-4 mb-4 w-full">
                                            <div className="flex justify-between items-center mb-2">
                                                <span className="font-serif text-primary font-medium">
                                                    Session in Progress
                                                    {sessionSummary.session_type && sessionSummary.session_type !== 'standard'
                                                        ? ` (${sessionSummary.session_type.replace('_', ' ')})`
                                                        : ''}
                                                </span>
                                                <span className="text-sm font-mono text-primary/60">
                                                    {reviewedCount} / {sessionSummary.requested_n}
                                                </span>
                                            </div>

                                            {/* Progress Bar */}
                                            <div className="h-2 bg-primary/10 rounded-full overflow-hidden">
                                                <div
                                                    className="h-full bg-primary transition-all duration-500 ease-out"
                                                    style={{ width: `${Math.min(100, (reviewedCount / sessionSummary.requested_n) * 100)}%` }}
                                                />
                                            </div>

                                            {/* Enhanced Session Stats */}
                                            <div className="flex justify-between mt-3 text-xs">
                                                <div className="flex items-center">
                                                    <span className="text-primary/60 mr-1">🔥</span>
                                                    <span className="text-primary/80">Streak: {streak}</span>
                                                    <span className="text-primary/40 mx-1">|</span>
                                                    <span className="text-primary/60 mr-1">🏆</span>
                                                    <span className="text-primary/80">Best: {bestStreak}</span>
                                                </div>
                                                <div className="flex items-center">
                                                    <span className="text-primary/60 mr-1">💡</span>
                                                    <span className="text-primary/80">Hints: {hintsUsed}</span>
                                                </div>
                                            </div>

                                            {/* Performance Visualization */}
                                            {performanceHistory.length > 0 && (
                                                <div className="mt-3">
                                                    <div className="flex justify-between text-xs text-primary/60 mb-1">
                                                        <span>Recent Performance:</span>
                                                        <span>{calculateRecentPerformance(performanceHistory)}% accuracy (5min)</span>
                                                    </div>
                                                    <div className="flex h-2 rounded-full overflow-hidden bg-primary/10">
                                                        {performanceHistory.slice(-10).map((item, index) => (
                                                            <div
                                                                key={index}
                                                                className={`flex-1 ${item.result === 'pass' ? 'bg-green-500' : 'bg-red-500'}`}
                                                                title={`${item.result.toUpperCase()} - ${new Date(item.time).toLocaleTimeString()}`}
                                                            />
                                                        ))}
                                                    </div>
                                                    <div className="flex justify-between text-xs text-primary/60 mt-1">
                                                        <span>
                                                            Trend:
                                                            <span className={`ml-1 ${getPerformanceTrend(performanceHistory) === 'improving' ? 'text-green-500' :
                                                                getPerformanceTrend(performanceHistory) === 'declining' ? 'text-red-500' : 'text-primary/60'
                                                                }`}>
                                                                {getPerformanceTrend(performanceHistory) === 'improving' ? '↗ Improving' :
                                                                    getPerformanceTrend(performanceHistory) === 'declining' ? '↘ Declining' : '→ Stable'}
                                                            </span>
                                                        </span>
                                                        <span>
                                                            Time: {currentPuzzleTime}s
                                                        </span>
                                                    </div>
                                                </div>
                                            )}

                                            {/* Timed Session Timer */}
                                            {sessionSummary.session_type === 'timed' && timeRemaining > 0 && (
                                                <div className="mt-2 text-center">
                                                    <span className={`font-mono text-sm ${timeRemaining < 60 ? 'text-red-500' : 'text-primary/80'}`}>
                                                        Time Remaining: {Math.floor(timeRemaining / 60)}:{(timeRemaining % 60).toString().padStart(2, '0')}
                                                    </span>
                                                </div>
                                            )}

                                            {isResumingSession && (
                                                <div className="text-xs text-center mt-2 text-primary/60 animate-pulse">
                                                    Resuming previous session...
                                                </div>
                                            )}
                                        </div>
                                    )}

                                    <div className="flex justify-between items-center">
                                        <div className="flex items-center gap-2">
                                            <span className="font-serif text-xl text-primary">
                                                {currentPuzzle.title || "Puzzle"}
                                                <span className="text-base font-normal opacity-50 ml-2 font-sans">
                                                    {currentIndex + 1} / {puzzles.length}
                                                </span>
                                            </span>
                                            {currentPuzzle.primary_motif && (
                                                <span className="text-sm font-sans text-primary/60 px-2 py-1 bg-primary/10 rounded-sm">
                                                    {currentPuzzle.primary_motif}
                                                </span>
                                            )}
                                        </div>
                                    </div>
                                </div>
                                <span className="font-sans text-sm tracking-wide uppercase text-primary/60">
                                    {currentPuzzle.side_to_move === 'white' ? 'White to Move' : 'Black to Move'}
                                </span>
                            </div>
                        </div>

                        {/* Status Area */}
                        <div className="min-h-[100px] flex items-center justify-center text-center p-6 border border-primary/10 rounded-sm relative overflow-hidden">
                            {status === 'solving' && clueStage === 0 && <p className="text-primary/60 font-serif text-lg italic">Find the best move...</p>}
                            {status === 'solving' && clueStage === 1 && (
                                <p className="text-primary/80 font-sans text-sm">
                                    {currentPuzzle?.best_move_uci
                                        ? getPieceNameAtSquare(currentPuzzle.fen, bestMoveParsed.from)
                                        : 'Move the correct piece'}
                                </p>
                            )}
                            {status === 'correct' && (
                                <div className="text-center">
                                    <p className="text-green-600 font-serif text-2xl animate-teedin">Correct! Excellent.</p>
                                    {lastFeedback && (
                                        <p className="text-green-600 font-sans text-sm mt-2 animate-teedin">{lastFeedback}</p>
                                    )}
                                </div>
                            )}
                            {status === 'incorrect' && (
                                <div className="text-center">
                                    <p className="text-red-500 font-serif text-2xl animate-teedin">Incorrect.</p>
                                    {lastFeedback && (
                                        <p className="text-red-500 font-sans text-sm mt-2 animate-teedin">{lastFeedback}</p>
                                    )}
                                </div>
                            )}
                            {status === 'revealed' && (
                                <div>
                                    <p className="text-primary/60 font-sans text-xs uppercase tracking-widest mb-1">Solution</p>
                                    <p className="text-primary font-mono text-xl">{currentPuzzle.best_move_uci}</p>
                                </div>
                            )}
                        </div>

                        {/* Actions */}
                        <div className="space-y-6">
                            {/* Type Move Toggle */}
                            <div className="flex justify-between items-center px-2">
                                <span className="text-xs text-primary/40 uppercase tracking-widest font-sans">Input Method</span>
                                <button
                                    type="button"
                                    onClick={() => setShowUciInput(!showUciInput)}
                                    className="km-interactive km-focus-visible km-inline-link text-primary text-xs font-serif underline decoration-primary/30 underline-offset-4 transition-colors">
                                    {showUciInput ? 'Switch to Drag & Drop' : 'Type Move Manually'}
                                </button>
                            </div>

                            {showUciInput && (
                                <div className="animate-switchedin">
                                    <input
                                        type="text"
                                        placeholder="e.g. e2e4"
                                        value={userMove}
                                        onChange={(e) => setUserMove(e.target.value)}
                                        className="w-full bg-primary/5 border border-primary/20 p-3 rounded-sm text-primary font-mono text-center focus:outline-none focus:border-primary/60 transition-colors"
                                        onKeyPress={(e) => e.key === 'Enter' && handleCheckAnswer()}
                                    />
                                </div>
                            )}

                            {status === 'solving' && (
                                <div className="grid grid-cols-3 gap-4">
                                    <button
                                        type="button"
                                        onClick={handleCheckAnswer}
                                        disabled={!userMove}
                                        className={`px-6 py-4 bg-primary text-bg-primary rounded-sm font-serif text-lg transition-all shadow-lg shadow-primary/5 km-focus-visible disabled:opacity-50 ${!userMove ? 'km-interactive-disabled' : 'km-interactive'}`}>
                                        Check Move
                                    </button>
                                    <button
                                        type="button"
                                        onClick={activeSessionId ? handleUseHint : handleClue}
                                        disabled={activeSessionId
                                            ? (!currentPuzzle?.best_move_uci || hintsUsed >= 3)
                                            : (!currentPuzzle?.best_move_uci || clueStage === 2)}
                                        className="px-6 py-4 border border-primary/20 text-primary rounded-sm font-serif text-lg transition-all km-interactive km-focus-visible disabled:opacity-50 disabled:cursor-default">
                                        {activeSessionId ? `Hint (${hintsUsed}/3)` : 'Clue'}
                                    </button>
                                    <button
                                        type="button"
                                        onClick={handleRevealSolution}
                                        className="px-6 py-4 border border-primary/20 text-primary rounded-sm font-serif text-lg transition-all km-interactive km-focus-visible">
                                        Reveal
                                    </button>
                                </div>
                            )}
                            {(status === 'correct' || status === 'revealed') && (
                                <div className="space-y-4">
                                    {/* Performance Stats for this puzzle */}
                                    {currentPuzzle?.attempts !== undefined && (
                                        <div className="bg-primary/5 p-3 rounded-sm text-sm">
                                            <div className="flex justify-between">
                                                <span className="text-primary/60">Puzzle Stats:</span>
                                                <span className="font-mono">
                                                    {currentPuzzle.pass_count || 0}/{currentPuzzle.attempts || 0}
                                                    {currentPuzzle.attempts ? ` (${Math.round(((currentPuzzle.pass_count || 0) / currentPuzzle.attempts) * 100)}%)` : ''}
                                                </span>
                                            </div>
                                            {currentPuzzle.next_due_at && (
                                                <div className="flex justify-between mt-1">
                                                    <span className="text-primary/60">Next Review:</span>
                                                    <span className="font-mono">
                                                        {new Date(currentPuzzle.next_due_at).toLocaleDateString()}
                                                    </span>
                                                </div>
                                            )}
                                        </div>
                                    )}

                                    <button
                                        type="button"
                                        onClick={handleAdvancePuzzle}
                                        disabled={finishButtonDisabled || sessionState === 'completing'}
                                        className={`w-full px-6 py-4 bg-green-600 text-white rounded-sm font-serif text-lg transition-all shadow-lg shadow-green-900/20 km-focus-visible ${finishButtonDisabled || sessionState === 'completing' ? 'km-interactive-disabled' : 'km-interactive'} flex items-center justify-center`}>
                                        {sessionState === 'completing' ? (
                                            <>
                                                <span className="animate-spin h-5 w-5 border-2 border-white/20 border-t-white rounded-full mr-2"></span>
                                                Recording Session...
                                            </>
                                        ) : isFinalPuzzle ? 'All Done' : 'Next Puzzle →'}
                                    </button>
                                </div>
                            )}
                            {status === 'incorrect' && (
                                <div className="space-y-4">
                                    {/* Detailed feedback for incorrect answers */}
                                    {lastFeedback && (
                                        <div className="bg-red-500/10 border border-red-500/20 p-3 rounded-sm text-sm">
                                            <p className="text-red-500 font-sans">{lastFeedback}</p>
                                        </div>
                                    )}

                                    <div className="grid grid-cols-2 gap-4">
                                        <button
                                            type="button"
                                            onClick={async () => {
                                                await handleReviewPuzzle('fail');
                                                setStatus('solving');
                                                setUserMove('');
                                                setGame(new Chess(currentPuzzle.fen));
                                                setClueStage(0);
                                            }}
                                            className="px-6 py-4 border border-primary/20 text-primary rounded-sm font-serif text-lg transition-all km-interactive km-focus-visible">
                                            Mark as Failed & Try Again
                                        </button>
                                        <button
                                            type="button"
                                            onClick={handleRevealSolution}
                                            className="px-6 py-4 bg-primary text-bg-primary rounded-sm font-serif text-lg transition-all km-interactive km-focus-visible">
                                            Show Solution
                                        </button>
                                    </div>
                                </div>
                            )}
                        </div>
                    </div>
                </section>
            )}

            {/* Session Summary */}
            {sessionSummary && (
                <section className="bg-primary/5 border border-green-500/30 rounded-sm p-8 backdrop-blur-sm animate-teedin">
                    <div className="flex items-center mb-6">
                        <div className="flex-shrink-0 h-8 w-8 rounded-full bg-green-500 flex items-center justify-center mr-3">
                            <svg className="h-5 w-5 text-white" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
                            </svg>
                        </div>
                        <h2 className="text-2xl font-serif text-primary">Session Successfully Recorded!</h2>
                    </div>

                    {sessionSummary.completed_at && (
                        <div className="text-sm text-primary/60 mb-4">
                            Completed on {new Date(sessionSummary.completed_at).toLocaleString()}
                        </div>
                    )}

                    <div className="grid grid-cols-2 md:grid-cols-4 gap-6 mb-6">
                        <div className="text-center">
                            <div className="text-3xl font-serif text-green-600">{sessionSummary.pass_count}</div>
                            <div className="text-xs uppercase tracking-widest text-primary/40 mt-1">Passed</div>
                        </div>
                        <div className="text-center">
                            <div className="text-3xl font-serif text-red-500">{sessionSummary.fail_count}</div>
                            <div className="text-xs uppercase tracking-widest text-primary/40 mt-1">Failed</div>
                        </div>
                        <div className="text-center">
                            <div className="text-3xl font-serif text-primary">
                                {calculateAccuracy(sessionSummary.pass_count, sessionSummary.fail_count)}%
                            </div>
                            <div className="text-xs uppercase tracking-widest text-primary/40 mt-1">Accuracy</div>
                        </div>
                        <div className="text-center">
                            <div className="text-3xl font-serif text-primary">
                                {Math.floor(sessionSummary.total_time_ms / 60000)}m {Math.floor((sessionSummary.total_time_ms % 60000) / 1000)}s
                            </div>
                            <div className="text-xs uppercase tracking-widest text-primary/40 mt-1">Total Time</div>
                        </div>
                    </div>

                    {/* Enhanced Session Stats */}
                    <div className="grid grid-cols-2 md:grid-cols-4 gap-6 mb-6">
                        <div className="text-center">
                            <div className="text-3xl font-serif text-primary">{sessionSummary.best_streak}</div>
                            <div className="text-xs uppercase tracking-widest text-primary/40 mt-1">Best Streak</div>
                        </div>
                        <div className="text-center">
                            <div className="text-3xl font-serif text-primary">{sessionSummary.hints_used}</div>
                            <div className="text-xs uppercase tracking-widest text-primary/40 mt-1">Hints Used</div>
                        </div>
                        {sessionSummary.session_type && sessionSummary.session_type !== 'standard' && (
                            <div className="text-center md:col-span-2">
                                <div className="text-xl font-serif text-primary capitalize">
                                    {sessionSummary.session_type.replace('_', ' ')}
                                    {sessionSummary.target_accuracy && ` (${sessionSummary.target_accuracy}% accuracy)`}
                                    {sessionSummary.target_time_minutes && ` (${sessionSummary.target_time_minutes} minutes)`}
                                </div>
                                <div className="text-xs uppercase tracking-widest text-primary/40 mt-1">Session Type</div>
                            </div>
                        )}
                    </div>

                    {/* Achievements Earned */}
                    {achievements.filter(a => a.earned).length > 0 && (
                        <div className="mb-6">
                            <h3 className="text-lg font-serif text-primary mb-3">Achievements Earned</h3>
                            <div className="flex flex-wrap gap-2">
                                {achievements.filter(a => a.earned).map(achievement => (
                                    <div
                                        key={achievement.id}
                                        className="flex items-center bg-primary/10 border border-primary/20 rounded-full px-3 py-1"
                                        title={achievement.description}
                                    >
                                        <span className="text-lg mr-2">{achievement.icon}</span>
                                        <span className="text-sm font-serif text-primary">{achievement.name}</span>
                                    </div>
                                ))}
                            </div>
                        </div>
                    )}

                    <button
                        type="button"
                        onClick={() => {
                            setSessionSummary(null);
                            setLastFeedback('');
                            handleStartSession();
                        }}
                        className="w-full px-6 py-3 bg-primary text-bg-primary rounded-sm font-serif transition-colors km-interactive km-focus-visible">
                        Start New Session
                    </button>
                </section>
            )}

            {/* Recent Sessions */}
            {recentSessions.length > 0 && (
                <section className="bg-primary/5 border border-primary/10 rounded-sm p-6 backdrop-blur-sm">
                    <h3 className="text-lg font-serif text-primary mb-4">Recent Sessions</h3>
                    <div className="space-y-2">
                        {recentSessions.map((session) => (
                            <div key={session.session_id} className="flex justify-between items-center p-3 bg-primary/5 rounded-sm text-sm">
                                <div className="flex gap-4">
                                    <span className="text-green-600">{session.pass_count}P</span>
                                    <span className="text-red-500">{session.fail_count}F</span>
                                    <span className="text-primary/60">
                                        {calculateAccuracy(session.pass_count, session.fail_count)}%
                                    </span>
                                    {session.best_streak > 0 && (
                                        <span className="text-primary/80">🔥{session.best_streak}</span>
                                    )}
                                </div>
                                <div className="flex gap-2">
                                    {session.session_type && session.session_type !== 'standard' && (
                                        <span className="text-primary/40 text-xs capitalize">
                                            {session.session_type.replace('_', ' ')}
                                        </span>
                                    )}
                                    <span className="text-primary/40 text-xs">
                                        {new Date(session.created_at).toLocaleDateString()}
                                    </span>
                                </div>
                            </div>
                        ))}
                    </div>
                </section>
            )}

            {/* Achievements Progress */}
            <section className="bg-primary/5 border border-primary/10 rounded-sm p-6 backdrop-blur-sm">
                <h3 className="text-lg font-serif text-primary mb-4">Achievements</h3>
                <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
                    {achievements.map(achievement => (
                        <div
                            key={achievement.id}
                            className={`p-4 rounded-sm border ${achievement.earned
                                ? 'bg-green-500/10 border-green-500/30'
                                : 'bg-primary/5 border-primary/20'
                                }`}
                        >
                            <div className="flex items-center">
                                <span className="text-2xl mr-3">{achievement.icon}</span>
                                <div>
                                    <h4 className={`font-serif ${achievement.earned ? 'text-green-600' : 'text-primary'}`}>
                                        {achievement.name}
                                    </h4>
                                    <p className="text-xs text-primary/60 mt-1">{achievement.description}</p>
                                    {achievement.earned && achievement.earnedAt && (
                                        <p className="text-xs text-green-600/80 mt-1">
                                            Earned: {achievement.earnedAt.toLocaleDateString()}
                                        </p>
                                    )}
                                </div>
                            </div>
                        </div>
                    ))}
                </div>
            </section>

            {/* Chess Pattern Mastery */}
            {motifPerformance && motifPerformance.motifs.length > 0 && (
                <section className="bg-primary/5 border border-primary/10 rounded-sm p-6 backdrop-blur-sm">
                    <h3 className="text-lg font-serif text-primary mb-4">Chess Pattern Mastery</h3>
                    <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
                        {motifPerformance.motifs.map(motif => (
                            <div
                                key={motif.name}
                                className={`p-4 rounded-sm border ${
                                    motif.rank === 'mastered'
                                        ? 'bg-green-500/10 border-green-500/30'
                                        : motif.rank === 'learning'
                                        ? 'bg-yellow-500/10 border-yellow-500/30'
                                        : 'bg-red-500/10 border-red-500/30'
                                }`}
                            >
                                <h4 className="font-serif text-primary mb-1">{motif.name}</h4>
                                <div className="flex justify-between text-sm">
                                    <span className="text-primary/60">
                                        {motif.passed}/{motif.total_puzzles} solved
                                    </span>
                                    <span className={`font-mono ${
                                        motif.rank === 'mastered' ? 'text-green-600' :
                                        motif.rank === 'learning' ? 'text-yellow-600' :
                                        'text-red-500'
                                    }`}>
                                        {Math.round(motif.accuracy * 100)}%
                                    </span>
                                </div>

                                {/* Progress bar */}
                                <div className="mt-2 h-2 bg-primary/10 rounded-full overflow-hidden">
                                    <div
                                        className={`h-full ${
                                            motif.rank === 'mastered' ? 'bg-green-500' :
                                            motif.rank === 'learning' ? 'bg-yellow-500' :
                                            'bg-red-500'
                                        }`}
                                        style={{ width: `${motif.accuracy * 100}%` }}
                                    />
                                </div>
                            </div>
                        ))}
                    </div>
                </section>
            )}
        </div>
    );
}
