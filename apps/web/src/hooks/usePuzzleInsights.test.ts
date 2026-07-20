import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, act, waitFor } from '@testing-library/react';
import { usePuzzleInsights } from './usePuzzleInsights';

const mockUserStatus = {
    username: 'testuser',
    games_count: 10,
    puzzles_count: 5,
    due_count: 3,
    next_due_at: null,
    has_new_games: true,
};

const mockMotifPerformance = {
    motifs: [{ name: 'fork', accuracy: 0.8, passed: 4, total_puzzles: 5, rank: 'learning' as const, attempts: 5, insufficient_data: false }],
    weakest_motifs: ['pin'],
    total_motifs_practiced: 3,
};

const mockRecentSessions = [
    {
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
    },
];

vi.mock('../api', () => ({
    getUserStatus: vi.fn(),
    getMotifPerformance: vi.fn(),
    getRecentSessions: vi.fn(),
}));

import { getUserStatus, getMotifPerformance, getRecentSessions } from '../api';

const mockedGetUserStatus = vi.mocked(getUserStatus);
const mockedGetMotifPerformance = vi.mocked(getMotifPerformance);
const mockedGetRecentSessions = vi.mocked(getRecentSessions);

describe('usePuzzleInsights', () => {
    beforeEach(() => {
        vi.clearAllMocks();
        mockedGetUserStatus.mockResolvedValue(mockUserStatus);
        mockedGetMotifPerformance.mockResolvedValue(mockMotifPerformance);
        mockedGetRecentSessions.mockResolvedValue(mockRecentSessions);
    });

    it('should not fetch when username is empty', () => {
        renderHook(() => usePuzzleInsights(''));

        expect(mockedGetUserStatus).not.toHaveBeenCalled();
        expect(mockedGetMotifPerformance).not.toHaveBeenCalled();
        expect(mockedGetRecentSessions).not.toHaveBeenCalled();
    });

    it('should fetch userStatus on mount', async () => {
        const { result } = renderHook(() => usePuzzleInsights('testuser'));

        expect(result.current.isLoadingStatus).toBe(true);

        await waitFor(() => {
            expect(result.current.userStatus).toEqual(mockUserStatus);
        });
        expect(result.current.isLoadingStatus).toBe(false);
    });

    it('should fetch motifPerformance on mount', async () => {
        const { result } = renderHook(() => usePuzzleInsights('testuser'));

        await waitFor(() => {
            expect(result.current.motifPerformance).toEqual(mockMotifPerformance);
        });
    });

    it('should fetch recentSessions on mount', async () => {
        const { result } = renderHook(() => usePuzzleInsights('testuser'));

        await waitFor(() => {
            expect(result.current.recentSessions).toEqual(mockRecentSessions);
        });
    });

    it('should handle userStatus API error', async () => {
        mockedGetUserStatus.mockRejectedValue(new Error('Network error'));
        // Prevent other effects from overwriting insightsError
        mockedGetMotifPerformance.mockImplementation(() => new Promise(() => {}));
        mockedGetRecentSessions.mockImplementation(() => new Promise(() => {}));
        const spy = vi.spyOn(console, 'warn').mockImplementation(() => {});

        const { result } = renderHook(() => usePuzzleInsights('testuser'));

        await waitFor(() => {
            expect(result.current.userStatus).toBeNull();
            expect(result.current.insightsError).toBe('Network error');
            expect(result.current.isLoadingStatus).toBe(false);
        });

        spy.mockRestore();
    });

    it('should handle motifPerformance API error', async () => {
        mockedGetMotifPerformance.mockRejectedValue(new Error('Motif error'));
        // Prevent other effects from overwriting insightsError
        mockedGetUserStatus.mockImplementation(() => new Promise(() => {}));
        mockedGetRecentSessions.mockImplementation(() => new Promise(() => {}));
        const spy = vi.spyOn(console, 'warn').mockImplementation(() => {});

        const { result } = renderHook(() => usePuzzleInsights('testuser'));

        await waitFor(() => {
            expect(result.current.motifPerformance).toBeNull();
            expect(result.current.insightsError).toBe('Motif error');
        });

        spy.mockRestore();
    });

    it('should handle recentSessions API error', async () => {
        mockedGetRecentSessions.mockRejectedValue(new Error('Sessions error'));
        // Prevent other effects from overwriting insightsError
        mockedGetUserStatus.mockImplementation(() => new Promise(() => {}));
        mockedGetMotifPerformance.mockImplementation(() => new Promise(() => {}));

        const { result } = renderHook(() => usePuzzleInsights('testuser'));

        await waitFor(() => {
            expect(result.current.insightsError).toBe('Sessions error');
        });
    });

    it('should refresh all insights via handleRefreshInsights', async () => {
        const { result } = renderHook(() => usePuzzleInsights('testuser'));

        // Wait for initial load
        await waitFor(() => {
            expect(result.current.userStatus).toEqual(mockUserStatus);
        });

        vi.clearAllMocks();
        const updatedStatus = { ...mockUserStatus, games_count: 20 };
        mockedGetUserStatus.mockResolvedValue(updatedStatus);
        mockedGetMotifPerformance.mockResolvedValue(mockMotifPerformance);
        mockedGetRecentSessions.mockResolvedValue(mockRecentSessions);

        await act(async () => {
            await result.current.handleRefreshInsights();
        });

        expect(mockedGetUserStatus).toHaveBeenCalledOnce();
        expect(mockedGetMotifPerformance).toHaveBeenCalledOnce();
        expect(mockedGetRecentSessions).toHaveBeenCalledOnce();
        expect(result.current.userStatus).toEqual(updatedStatus);
        expect(result.current.isRefreshingInsights).toBe(false);
    });

    it('should cancel in-flight requests on username change', async () => {
        const { result, rerender } = renderHook(
            ({ username }) => usePuzzleInsights(username),
            { initialProps: { username: 'user1' } },
        );

        // Wait for initial load
        await waitFor(() => {
            expect(result.current.userStatus).toEqual(mockUserStatus);
        });

        // Change username; previous effects should be cancelled
        const user2Status = { ...mockUserStatus, username: 'user2' };
        mockedGetUserStatus.mockResolvedValue(user2Status);

        rerender({ username: 'user2' });

        await waitFor(() => {
            expect(result.current.userStatus).toEqual(user2Status);
        });
    });

    it('should clear state when username becomes empty', async () => {
        const { result, rerender } = renderHook(
            ({ username }) => usePuzzleInsights(username),
            { initialProps: { username: 'testuser' } },
        );

        await waitFor(() => {
            expect(result.current.userStatus).toEqual(mockUserStatus);
        });

        rerender({ username: '' });

        expect(result.current.userStatus).toBeNull();
        expect(result.current.motifPerformance).toBeNull();
        expect(result.current.recentSessions).toEqual([]);
    });

    it('should have stable callback references', async () => {
        const { result, rerender } = renderHook(() => usePuzzleInsights('testuser'));

        await waitFor(() => {
            expect(result.current.userStatus).toEqual(mockUserStatus);
        });

        const firstRefresh = result.current.handleRefreshInsights;
        const firstRefreshStatus = result.current.refreshUserStatus;

        rerender();

        expect(result.current.handleRefreshInsights).toBe(firstRefresh);
        expect(result.current.refreshUserStatus).toBe(firstRefreshStatus);
    });
});
