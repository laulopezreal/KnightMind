import { request } from './core';

export interface SessionSummary {
    session_id: string;
    requested_n: number;
    pass_count: number;
    fail_count: number;
    total_time_ms: number;
    created_at: string;
    completed_at: string | null;
    // Enhanced session fields
    session_type?: string;
    target_accuracy?: number;
    target_time_minutes?: number;
    current_streak: number;
    best_streak: number;
    hints_used: number;
}

export async function getSession(sessionId: string): Promise<SessionSummary> {
    return await request<SessionSummary>(`/sessions/${sessionId}`);
}

export async function startSession(
    username: string,
    n: number = 5,
    sessionType: string = "standard",
    targetAccuracy?: number,
    targetTimeMinutes?: number,
    sessionData?: Record<string, unknown>
): Promise<{ session_id: string; requested_n: number; session_type?: string; target_accuracy?: number; target_time_minutes?: number }> {
    return await request<{ session_id: string; requested_n: number; session_type?: string; target_accuracy?: number; target_time_minutes?: number }>(`/sessions/start`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            username,
            n,
            session_type: sessionType,
            target_accuracy: targetAccuracy,
            target_time_minutes: targetTimeMinutes,
            session_data: sessionData
        }),
    });
}

export async function completeSession(sessionId: string, username: string): Promise<SessionSummary> {
    return await request<SessionSummary>(`/sessions/${sessionId}/complete`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username }),
    });
}

export async function getRecentSessions(username: string, limit: number = 10): Promise<SessionSummary[]> {
    const params = new URLSearchParams({
        username,
        limit: limit.toString(),
    });

    return await request<SessionSummary[]>(`/sessions/recent?${params}`);
}

export async function useHint(sessionId: string, username: string): Promise<SessionSummary> {
    return await request<SessionSummary>(`/sessions/${sessionId}/use_hint`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username }),
    });
}
