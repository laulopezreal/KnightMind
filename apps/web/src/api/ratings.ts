import { API_BASE } from './core';

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
    expected_minus_actual: number | null;
    missing_opponent_rating_games: number;
}

export interface ExplainResponse {
    time_control: string;
    window: RatingWindow;
    rating: RatingInfo;
    stats: DriverStats;
    drivers: string[];
    highlights: Highlights;
}

export const createSnapshot = async (username: string, timeControl: string): Promise<SnapshotResponse> => {
    const response = await fetch(`${API_BASE}/ratings/snapshot`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username, time_control: timeControl }),
    });
    if (!response.ok) throw new Error('Failed to create snapshot');
    return response.json();
};

export const getRatingExplain = async (
    username: string,
    timeControl: string = 'rapid',
    sinceSessionId?: string,
    since?: string
): Promise<ExplainResponse> => {
    const params = new URLSearchParams({
        username,
        time_control: timeControl,
        limit_games: '200'
    });
    if (sinceSessionId) {
        params.append('since_session_id', sinceSessionId);
    }
    if (since) {
        params.append('since', since);
    }

    const response = await fetch(`${API_BASE}/ratings/explain?${params.toString()}`);
    if (!response.ok) throw new Error('Failed to fetch rating explainer');
    return response.json();
};
