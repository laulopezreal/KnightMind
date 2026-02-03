import { useCallback, useEffect, useState } from 'react';
import {
    getRecentSessions,
    getUserStatus,
    getMotifPerformance,
    type UserStatus,
    type MotifPerformanceResponse,
    type SessionSummary,
} from '../api';

export interface UsePuzzleInsightsReturn {
    userStatus: UserStatus | null;
    isLoadingStatus: boolean;
    motifPerformance: MotifPerformanceResponse | null;
    recentSessions: SessionSummary[];
    insightsError: string | null;
    isRefreshingInsights: boolean;
    refreshUserStatus: () => Promise<void>;
    refreshMotifPerformance: () => Promise<void>;
    refreshRecentSessions: () => Promise<void>;
    handleRefreshInsights: () => Promise<void>;
    /** Direct setter for recentSessions (used by session completion). */
    setRecentSessions: React.Dispatch<React.SetStateAction<SessionSummary[]>>;
    /** Direct setter for motifPerformance (used by session completion). */
    setMotifPerformance: React.Dispatch<React.SetStateAction<MotifPerformanceResponse | null>>;
}

export function usePuzzleInsights(username: string): UsePuzzleInsightsReturn {
    const [userStatus, setUserStatus] = useState<UserStatus | null>(null);
    const [isLoadingStatus, setIsLoadingStatus] = useState(false);
    const [motifPerformance, setMotifPerformance] = useState<MotifPerformanceResponse | null>(null);
    const [recentSessions, setRecentSessions] = useState<SessionSummary[]>([]);
    const [isRefreshingInsights, setIsRefreshingInsights] = useState(false);
    const [insightsError, setInsightsError] = useState<string | null>(null);

    // Fetch user status on username change
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
                    setInsightsError(null);
                }
            } catch (err) {
                if (!cancelled) {
                    console.warn('Unable to load user status:', err);
                    setUserStatus(null);
                    setInsightsError(err instanceof Error ? err.message : 'Unable to load user status');
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

    // Fetch motif performance on username change
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
                    setInsightsError(null);
                }
            } catch (err) {
                if (!cancelled) {
                    console.warn('Unable to load motif performance:', err);
                    setMotifPerformance(null);
                    setInsightsError(err instanceof Error ? err.message : 'Unable to load motif performance');
                }
            }
        };

        fetchMotifs();

        return () => {
            cancelled = true;
        };
    }, [username]);

    // Fetch recent sessions on username change
    useEffect(() => {
        if (!username) {
            setRecentSessions([]);
            return;
        }

        const loadRecent = async () => {
            try {
                const sessions = await getRecentSessions(username, 5);
                setRecentSessions(sessions);
                setInsightsError(null);
            } catch (err) {
                setInsightsError(err instanceof Error ? err.message : 'Failed to load recent sessions');
            }
        };

        loadRecent();
    }, [username]);

    const refreshUserStatus = useCallback(async () => {
        if (!username) return;
        try {
            const status = await getUserStatus(username);
            setUserStatus(status);
        } catch (err) {
            console.warn('Unable to refresh user status:', err);
            setUserStatus(null);
            setInsightsError(err instanceof Error ? err.message : 'Unable to refresh user status');
        }
    }, [username]);

    const refreshMotifPerformance = useCallback(async () => {
        if (!username) return;
        try {
            const performance = await getMotifPerformance(username);
            setMotifPerformance(performance);
        } catch (err) {
            console.warn('Unable to refresh motif performance:', err);
            setMotifPerformance(null);
            setInsightsError(err instanceof Error ? err.message : 'Unable to load motif performance');
        }
    }, [username]);

    const refreshRecentSessions = useCallback(async () => {
        if (!username) return;
        try {
            const sessions = await getRecentSessions(username, 5);
            setRecentSessions(sessions);
        } catch (err) {
            console.warn('Unable to refresh recent sessions:', err);
            setRecentSessions([]);
            setInsightsError(err instanceof Error ? err.message : 'Unable to load recent sessions');
        }
    }, [username]);

    const handleRefreshInsights = useCallback(async () => {
        if (!username) return;
        setIsRefreshingInsights(true);
        setInsightsError(null);
        try {
            await Promise.all([refreshUserStatus(), refreshMotifPerformance(), refreshRecentSessions()]);
        } finally {
            setIsRefreshingInsights(false);
        }
    }, [refreshMotifPerformance, refreshRecentSessions, refreshUserStatus, username]);

    return {
        userStatus,
        isLoadingStatus,
        motifPerformance,
        recentSessions,
        insightsError,
        isRefreshingInsights,
        refreshUserStatus,
        refreshMotifPerformance,
        refreshRecentSessions,
        handleRefreshInsights,
        setRecentSessions,
        setMotifPerformance,
    };
}
