import { request, ApiError } from './core';

export interface HighlightGame {
    opponent_rating: number | null;
    opponent_username?: string | null;
    result: string;
    expected_score: number;
    rating_diff: number | null;
    game_id: string;
    played_at: string;
    url: string;
}

export interface Highlights {
    best_surprises: HighlightGame[];
    worst_surprises: HighlightGame[];
}

export interface RatingWindow {
    start: string;
    end: string;
    source: string;
}

export interface RatingInfo {
    start: number | null;
    end: number | null;
    net_change: number | null;
    // True when start/end were estimated from per-game Elo headers rather
    // than recorded snapshots. Older payloads only send this conflated flag;
    // newer ones also send the per-anchor flags below.
    is_estimated?: boolean;
    start_is_estimated?: boolean;
    end_is_estimated?: boolean;
    reference_rating: number;
    reference_is_approx: boolean;
}

export interface TrajectoryPoint {
    played_at: string;
    rating: number;
}

export interface ChartPoint {
    at: string;
    rating: number;
    source: 'game' | 'snapshot';
}

export interface DriverStats {
    games: number;
    wins: number;
    draws: number;
    losses: number;
    avg_opponent_rating: number | null;
    expected_total: number | null;
    actual_total: number | null;
    actual_minus_expected: number | null;
    missing_opponent_rating_games: number;
    // In-window casual (unrated) games excluded from attribution.
    casual_games_excluded?: number;
}

export interface Driver {
    text: string;
    severity: 'major' | 'moderate' | 'minor';
    direction: 'up' | 'down' | 'neutral';
}

export interface ExplainResponse {
    time_control: string;
    window: RatingWindow;
    rating: RatingInfo;
    stats: DriverStats;
    drivers: Driver[];
    highlights: Highlights;
    // Player's own rating over the window, from per-game PGN Elo headers.
    // Superseded by chart_series; kept for older payloads.
    trajectory?: TrajectoryPoint[];
    // Server-fused, chart-ready series (game Elo points + winning snapshot
    // anchors, time-ordered). Its endpoints match rating.start/rating.end.
    // Empty/absent when the window has no game points.
    chart_series?: ChartPoint[];
    // Canonical server-side uncertainty signal (from rated-game count).
    confidence: 'low' | 'medium' | 'high';
    insufficient_data: boolean;
}

export interface SnapshotHistoryItem {
    rating: number;
    recorded_at: string;
}

export const getRatingHistory = (
    username: string,
    timeControl: string = 'rapid',
    limit: number = 50
): Promise<SnapshotHistoryItem[]> => {
    const params = new URLSearchParams({ username, time_control: timeControl, limit: limit.toString() });
    return request<SnapshotHistoryItem[]>(`/ratings/history?${params.toString()}`);
};

function isPlainObject(value: unknown): value is Record<string, unknown> {
    return typeof value === 'object' && value !== null && !Array.isArray(value);
}

/**
 * A 200 does not guarantee the body matches `ExplainResponse`. Rating Insights
 * and the dashboard card walk `rating`, `stats`, `window`, `drivers` and
 * `highlights` unconditionally — `data.rating.net_change`,
 * `data.highlights.worst_surprises.length` and friends — so a body missing any
 * of them throws mid-render, which is how a page took the whole shell down.
 * Failing here routes it to the error state both callers already render
 * (Rating Insights shows this message; the dashboard drops the card, because
 * its fetch is one leg of a Promise.allSettled).
 *
 * Only the containers the UI dereferences without a guard are checked. Scalars
 * are deliberately not: `confidence` degrades to "low" in ConfidenceBadge and
 * the numeric fields are read defensively, so demanding them would reject
 * payloads the UI can display honestly — the opposite of the goal.
 */
function assertExplainResponse(value: unknown): ExplainResponse {
    const candidate = value as Partial<ExplainResponse> | null;
    const malformed =
        !isPlainObject(candidate) ||
        !isPlainObject(candidate.rating) ||
        !isPlainObject(candidate.stats) ||
        !isPlainObject(candidate.window) ||
        !isPlainObject(candidate.highlights) ||
        !Array.isArray(candidate.highlights?.best_surprises) ||
        !Array.isArray(candidate.highlights?.worst_surprises) ||
        !Array.isArray(candidate.drivers);

    if (malformed) {
        throw new ApiError(
            'Rating insights returned an unexpected response. Please try again.',
            502,
            `Malformed /ratings/explain payload: ${JSON.stringify(value)?.slice(0, 200)}`,
        );
    }
    return candidate as ExplainResponse;
}

export const getRatingExplain = async (
    username: string,
    timeControl: string = 'rapid',
    sinceSessionId?: string,
    since?: string,
    limitGames: number = 200
): Promise<ExplainResponse> => {
    const params = new URLSearchParams({
        username,
        time_control: timeControl,
        limit_games: limitGames.toString()
    });
    if (sinceSessionId) {
        params.append('since_session_id', sinceSessionId);
    }
    if (since) {
        params.append('since', since);
    }

    return assertExplainResponse(
        await request<ExplainResponse>(`/ratings/explain?${params.toString()}`),
    );
};
