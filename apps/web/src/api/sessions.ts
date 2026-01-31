import { request } from './core';

export interface SessionSummary {
    session_id: string;
    requested_n: number;
    pass_count: number;
    fail_count: number;
    total_time_ms: number;
    created_at: string;
    completed_at: string | null;
}

export async function getSession(sessionId: string): Promise<SessionSummary> {
    return await request<SessionSummary>(`/sessions/${sessionId}`);
}

export async function startSession(username: string, n: number = 5): Promise<{ session_id: string; requested_n: number }> {
    return await request<{ session_id: string; requested_n: number }>(`/sessions/start`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username, n }),
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
