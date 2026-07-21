import { request } from './core';

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
    // than recorded snapshots.
    is_estimated?: boolean;
    reference_rating: number;
    reference_is_approx: boolean;
}

export interface TrajectoryPoint {
    played_at: string;
    rating: number;
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
    trajectory?: TrajectoryPoint[];
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

export const getRatingExplain = (
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

    return request<ExplainResponse>(`/ratings/explain?${params.toString()}`);
};
