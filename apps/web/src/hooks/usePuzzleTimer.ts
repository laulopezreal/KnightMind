import { useCallback, useEffect, useRef, useState } from 'react';

export interface UsePuzzleTimerOptions {
    sessionType: string;
    activeSessionId: string | null;
    /** Called when the per-puzzle 30 s timeout fires (timed mode only). */
    onPuzzleTimeout?: () => void;
}

export interface UsePuzzleTimerReturn {
    /** Seconds remaining in timed-session countdown. */
    timeRemaining: number;
    /** Seconds elapsed on current puzzle. */
    currentPuzzleTime: number;
    /** Epoch ms when current puzzle was started, or null. */
    puzzleStartTime: number | null;
    /** Start (or restart) the per-puzzle elapsed timer + 30 s timeout. */
    startPuzzleTimer: () => void;
    /** Start the session countdown from `totalSeconds`. Calls `onTimeUp` at 0. */
    startSessionTimer: (totalSeconds: number, onTimeUp: () => void) => void;
    /** Clear all running timers immediately. */
    cleanup: () => void;
}

export function usePuzzleTimer({
    sessionType,
    activeSessionId,
    onPuzzleTimeout,
}: UsePuzzleTimerOptions): UsePuzzleTimerReturn {
    const puzzleTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
    const sessionTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
    const puzzleTimeRef = useRef<ReturnType<typeof setInterval> | null>(null);

    const [timeRemaining, setTimeRemaining] = useState(0);
    const [currentPuzzleTime, setCurrentPuzzleTime] = useState(0);
    const [puzzleStartTime, setPuzzleStartTime] = useState<number | null>(null);

    // Keep callback in a ref so the timeout always invokes the latest version.
    const onPuzzleTimeoutRef = useRef(onPuzzleTimeout);
    useEffect(() => {
        onPuzzleTimeoutRef.current = onPuzzleTimeout;
    });

    const cleanup = useCallback(() => {
        if (puzzleTimerRef.current) {
            clearTimeout(puzzleTimerRef.current);
            puzzleTimerRef.current = null;
        }
        if (sessionTimerRef.current) {
            clearInterval(sessionTimerRef.current);
            sessionTimerRef.current = null;
        }
        if (puzzleTimeRef.current) {
            clearInterval(puzzleTimeRef.current);
            puzzleTimeRef.current = null;
        }
    }, []);

    const startPuzzleTimer = useCallback(() => {
        // Clear existing puzzle-level timers
        if (puzzleTimerRef.current) clearTimeout(puzzleTimerRef.current);
        if (puzzleTimeRef.current) clearInterval(puzzleTimeRef.current);

        const startTime = Date.now();
        setPuzzleStartTime(startTime);
        setCurrentPuzzleTime(0);

        // 30 s auto-fail for timed sessions
        if (sessionType === 'timed' && activeSessionId) {
            puzzleTimerRef.current = setTimeout(() => {
                onPuzzleTimeoutRef.current?.();
            }, 30000);
        }

        // Elapsed-time counter (updates every 1 s)
        puzzleTimeRef.current = setInterval(() => {
            setCurrentPuzzleTime(Math.floor((Date.now() - startTime) / 1000));
        }, 1000);
    }, [sessionType, activeSessionId]);

    const startSessionTimer = useCallback(
        (totalSeconds: number, onTimeUp: () => void) => {
            setTimeRemaining(totalSeconds);
            if (sessionTimerRef.current) clearInterval(sessionTimerRef.current);

            // Store the callback in a ref so we can call it from inside the interval
            const onTimeUpRef = { current: onTimeUp };

            sessionTimerRef.current = setInterval(() => {
                setTimeRemaining(prev => {
                    if (prev <= 1) {
                        if (sessionTimerRef.current) clearInterval(sessionTimerRef.current);
                        onTimeUpRef.current();
                        return 0;
                    }
                    return prev - 1;
                });
            }, 1000);
        },
        [],
    );

    // Cleanup on unmount
    useEffect(() => cleanup, [cleanup]);

    return {
        timeRemaining,
        currentPuzzleTime,
        puzzleStartTime,
        startPuzzleTimer,
        startSessionTimer,
        cleanup,
    };
}
