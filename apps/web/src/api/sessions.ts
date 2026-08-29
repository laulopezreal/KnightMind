import { request } from './core';
import type { Puzzle } from './puzzles';

export type FocusPracticeReviewPolicy = 'normal_review' | 'practice_only';

export interface FocusPracticePuzzle extends Puzzle {
    review_policy: FocusPracticeReviewPolicy;
    queue_reason: { reason: 'practice'; explanation: string };
}

export interface FocusPracticeStartResponse {
    session_id: string;
    session_type: 'focus_practice';
    focus: { cause: string; name: string };
    requested_n: number;
    returned_count: number;
    puzzles: FocusPracticePuzzle[];
}

/** A puzzle the user failed in a session, with its server-owned diagnosis cause.
 *  No solutions, FEN, or best moves are included. */
export interface MissedPuzzleSummary {
    puzzle_id: string;
    display_name: string;
    cause: string | null;
    cause_label: string | null;
}

// Kept in sync with SessionSummary on the API.
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
    /** The focus this session was served with, if any. Resume orders by this
     *  rather than by the URL, so navigating back without the query parameter
     *  cannot shift which puzzle index N is. */
    focus_cause?: string | null;
    focus_opening?: string | null;
    focus_opening_scope?: string | null;
    /** The motif this session was filtered to, if any. Resume re-fetches with
     *  this rather than the URL's, so returning via the nav bar cannot widen
     *  the queue and shift which puzzle index N is. */
    motif?: string | null;
    selected_items?: Array<{ puzzle_id: string; position: number; review_policy: FocusPracticeReviewPolicy }> | null;
    puzzles?: FocusPracticePuzzle[] | null;
    focus_name?: string | null;
    /** Missed puzzles with server-owned cause labels. Only set on completed
     *  sessions with at least one failure. No FEN or solutions included. */
    missed_puzzles?: MissedPuzzleSummary[] | null;
}

// Kept in sync with SessionSummary on the API.
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

export async function startFocusPractice(username: string, focusCause: string, n = 5): Promise<FocusPracticeStartResponse> {
    return await request<FocusPracticeStartResponse>('/sessions/focus-practice/start', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username, focus_cause: focusCause, n }),
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
