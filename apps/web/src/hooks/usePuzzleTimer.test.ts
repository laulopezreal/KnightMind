import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { usePuzzleTimer } from './usePuzzleTimer';

describe('usePuzzleTimer', () => {
    beforeEach(() => {
        vi.useFakeTimers();
    });

    afterEach(() => {
        vi.useRealTimers();
    });

    it('should have correct initial state', () => {
        const { result } = renderHook(() =>
            usePuzzleTimer({ sessionType: 'standard', activeSessionId: null }),
        );

        expect(result.current.timeRemaining).toBe(0);
        expect(result.current.currentPuzzleTime).toBe(0);
        expect(result.current.puzzleStartTime).toBeNull();
    });

    it('should set puzzleStartTime when startPuzzleTimer is called', () => {
        vi.setSystemTime(new Date('2025-01-01T00:00:00Z'));

        const { result } = renderHook(() =>
            usePuzzleTimer({ sessionType: 'standard', activeSessionId: null }),
        );

        act(() => result.current.startPuzzleTimer());

        expect(result.current.puzzleStartTime).toBe(Date.now());
    });

    it('should increment currentPuzzleTime each second', () => {
        const { result } = renderHook(() =>
            usePuzzleTimer({ sessionType: 'standard', activeSessionId: null }),
        );

        act(() => result.current.startPuzzleTimer());

        expect(result.current.currentPuzzleTime).toBe(0);

        act(() => vi.advanceTimersByTime(1000));
        expect(result.current.currentPuzzleTime).toBe(1);

        act(() => vi.advanceTimersByTime(2000));
        expect(result.current.currentPuzzleTime).toBe(3);
    });

    it('should fire onPuzzleTimeout after 30s in timed mode', () => {
        const onTimeout = vi.fn();

        const { result } = renderHook(() =>
            usePuzzleTimer({
                sessionType: 'timed',
                activeSessionId: 'session-1',
                onPuzzleTimeout: onTimeout,
            }),
        );

        act(() => result.current.startPuzzleTimer());

        act(() => vi.advanceTimersByTime(29999));
        expect(onTimeout).not.toHaveBeenCalled();

        act(() => vi.advanceTimersByTime(1));
        expect(onTimeout).toHaveBeenCalledOnce();
    });

    it('should NOT fire timeout in standard mode', () => {
        const onTimeout = vi.fn();

        const { result } = renderHook(() =>
            usePuzzleTimer({
                sessionType: 'standard',
                activeSessionId: 'session-1',
                onPuzzleTimeout: onTimeout,
            }),
        );

        act(() => result.current.startPuzzleTimer());
        act(() => vi.advanceTimersByTime(60000));

        expect(onTimeout).not.toHaveBeenCalled();
    });

    it('should NOT fire timeout when no activeSessionId', () => {
        const onTimeout = vi.fn();

        const { result } = renderHook(() =>
            usePuzzleTimer({
                sessionType: 'timed',
                activeSessionId: null,
                onPuzzleTimeout: onTimeout,
            }),
        );

        act(() => result.current.startPuzzleTimer());
        act(() => vi.advanceTimersByTime(60000));

        expect(onTimeout).not.toHaveBeenCalled();
    });

    it('should count down session timer and call onTimeUp at 0', () => {
        const onTimeUp = vi.fn();

        const { result } = renderHook(() =>
            usePuzzleTimer({ sessionType: 'timed', activeSessionId: 'session-1' }),
        );

        act(() => result.current.startSessionTimer(5, onTimeUp));

        expect(result.current.timeRemaining).toBe(5);

        act(() => vi.advanceTimersByTime(1000));
        expect(result.current.timeRemaining).toBe(4);

        act(() => vi.advanceTimersByTime(3000));
        expect(result.current.timeRemaining).toBe(1);

        act(() => vi.advanceTimersByTime(1000));
        expect(result.current.timeRemaining).toBe(0);
        expect(onTimeUp).toHaveBeenCalledOnce();
    });

    it('should stop session timer after reaching 0', () => {
        const onTimeUp = vi.fn();

        const { result } = renderHook(() =>
            usePuzzleTimer({ sessionType: 'timed', activeSessionId: 'session-1' }),
        );

        act(() => result.current.startSessionTimer(2, onTimeUp));

        // Advance one tick at a time to let clearInterval take effect
        act(() => vi.advanceTimersByTime(1000));
        expect(result.current.timeRemaining).toBe(1);
        expect(onTimeUp).not.toHaveBeenCalled();

        act(() => vi.advanceTimersByTime(1000));
        expect(result.current.timeRemaining).toBe(0);
        expect(onTimeUp).toHaveBeenCalledOnce();

        // Further ticks should not re-invoke the callback
        act(() => vi.advanceTimersByTime(3000));
        expect(onTimeUp).toHaveBeenCalledOnce();
    });

    it('should stop all timers on cleanup()', () => {
        const onTimeout = vi.fn();
        const onTimeUp = vi.fn();

        const { result } = renderHook(() =>
            usePuzzleTimer({
                sessionType: 'timed',
                activeSessionId: 'session-1',
                onPuzzleTimeout: onTimeout,
            }),
        );

        act(() => {
            result.current.startPuzzleTimer();
            result.current.startSessionTimer(60, onTimeUp);
        });

        act(() => result.current.cleanup());
        act(() => vi.advanceTimersByTime(60000));

        expect(onTimeout).not.toHaveBeenCalled();
        expect(onTimeUp).not.toHaveBeenCalled();
    });

    it('should clear previous puzzle timers when startPuzzleTimer is called again', () => {
        const onTimeout = vi.fn();

        const { result } = renderHook(() =>
            usePuzzleTimer({
                sessionType: 'timed',
                activeSessionId: 'session-1',
                onPuzzleTimeout: onTimeout,
            }),
        );

        act(() => result.current.startPuzzleTimer());
        act(() => vi.advanceTimersByTime(15000)); // 15s into first puzzle

        // Start a new puzzle — should reset the 30s timer
        act(() => result.current.startPuzzleTimer());

        act(() => vi.advanceTimersByTime(15000)); // only 15s into second puzzle
        expect(onTimeout).not.toHaveBeenCalled();

        act(() => vi.advanceTimersByTime(15000)); // now 30s into second puzzle
        expect(onTimeout).toHaveBeenCalledOnce();
    });

    it('should cleanup timers on unmount', () => {
        const onTimeout = vi.fn();

        const { result, unmount } = renderHook(() =>
            usePuzzleTimer({
                sessionType: 'timed',
                activeSessionId: 'session-1',
                onPuzzleTimeout: onTimeout,
            }),
        );

        act(() => result.current.startPuzzleTimer());
        unmount();
        act(() => vi.advanceTimersByTime(60000));

        expect(onTimeout).not.toHaveBeenCalled();
    });

    it('should use latest onPuzzleTimeout callback via ref', () => {
        const firstCallback = vi.fn();
        const secondCallback = vi.fn();

        const { result, rerender } = renderHook(
            ({ onPuzzleTimeout }) =>
                usePuzzleTimer({
                    sessionType: 'timed',
                    activeSessionId: 'session-1',
                    onPuzzleTimeout,
                }),
            { initialProps: { onPuzzleTimeout: firstCallback } },
        );

        act(() => result.current.startPuzzleTimer());

        // Update the callback before timeout fires
        rerender({ onPuzzleTimeout: secondCallback });

        act(() => vi.advanceTimersByTime(30000));

        expect(firstCallback).not.toHaveBeenCalled();
        expect(secondCallback).toHaveBeenCalledOnce();
    });
});
