import { request, ApiError } from './core';

export interface Puzzle {
    id: string;
    username: string;
    source_game_id: string;
    ply: number;
    fen: string;
    side_to_move: string;
    played_move_uci: string;
    best_move_uci: string;
    eval_before: number;
    eval_after: number;
    swing: number;
    created_at: string;
    used_on: string | null;
    title?: string;
    primary_motif?: string;
    // Stats
    next_due_at?: string;
    interval_days?: number;
    ease_factor?: number;
    // Review stats
    attempts?: number;
    pass_count?: number;
    fail_count?: number;
    last_reviewed_at?: string;
    last_result?: string;
}

export interface DailyPuzzlesResponse {
    puzzles: Puzzle[];
    count: number;
}

export interface DuePuzzlesResponse {
    due_count: number;
    returned_count: number;
    now: string;
    puzzles: Puzzle[];
}

export interface ReviewPuzzleResponse {
    next_due_at: string;
    interval_days: number;
    ease_factor: number;
    feedback: string;
    puzzle_info: {
        fen: string;
        best_move: string;
        side_to_move: string;
        swing: number;
    };
    stats: {
        attempts: number;
        pass_count: number;
        fail_count: number;
        last_reviewed_at: string;
        last_result: string;
    };
}

export async function generatePuzzles(
    username: string,
    maxGames: number = 30,
    maxPuzzles: number = 30
): Promise<{ job_id: string }> {
    const params = new URLSearchParams({
        username,
        max_games: maxGames.toString(),
        max_puzzles: maxPuzzles.toString(),
    });

    try {
        return await request<{ job_id: string }>(`/puzzles/generate?${params}`, {
            method: 'POST',
        });
    } catch (err) {
        if (err instanceof ApiError && err.statusCode === 404) {
            throw new ApiError('No games found for user', 404, err.detail);
        }
        throw err;
    }
}

export async function getDailyPuzzles(
    username: string,
    n: number = 5
): Promise<DailyPuzzlesResponse> {
    const params = new URLSearchParams({
        username,
        n: n.toString(),
    });

    try {
        return await request<DailyPuzzlesResponse>(`/puzzles/daily?${params}`);
    } catch (err) {
        if (err instanceof ApiError && err.statusCode === 404) {
            throw new ApiError('No puzzles found', 404, err.detail);
        }
        throw err;
    }
}

export async function getDuePuzzles(
    username: string,
    n: number = 5,
    sessionType: string = "standard",
    targetAccuracy?: number
): Promise<DuePuzzlesResponse> {
    const params = new URLSearchParams({
        username,
        n: n.toString(),
        session_type: sessionType,
    });
    
    if (targetAccuracy !== undefined) {
        params.append('target_accuracy', targetAccuracy.toString());
    }

    try {
        return await request<DuePuzzlesResponse>(`/puzzles/due?${params}`);
    } catch (err) {
        if (err instanceof ApiError && err.statusCode === 404) {
            throw new ApiError('No puzzles found', 404, err.detail);
        }
        throw err;
    }
}

export async function reviewPuzzle(
    puzzleId: string,
    username: string,
    result: 'pass' | 'fail',
    timeSpentMs?: number,
    sessionId?: string
): Promise<ReviewPuzzleResponse> {
    return await request<ReviewPuzzleResponse>(`/puzzles/${puzzleId}/review`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            username,
            result,
            time_spent_ms: timeSpentMs,
            session_id: sessionId,
        }),
    });
}
