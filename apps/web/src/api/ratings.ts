import { request } from './core';

export interface SnapshotResponse {
    rating: number;
    recorded_at: string;
}

export interface HighlightGame {
    opponent_rating: number | null;
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
    reference_rating: number;
    reference_is_approx: boolean;
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

export const createSnapshot = (username: string, timeControl: string): Promise<SnapshotResponse> => {
    return request<SnapshotResponse>('/ratings/snapshot', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username, time_control: timeControl }),
    });
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
