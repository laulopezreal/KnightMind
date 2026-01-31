import { request, ApiError } from './core';

export interface ImportResult {
    message: string;
    games_count: number;
    new_games: number;
    skipped_duplicates: number;
}

export async function getUsers(): Promise<string[]> {
    const data = await request<{ users: string[] }>('/users');
    return data.users;
}

export async function importChessComGames(username: string): Promise<ImportResult> {
    try {
        return await request<ImportResult>(`/import/chesscom?username=${encodeURIComponent(username)}`, {
            method: 'POST',
        });
    } catch (err) {
        if (err instanceof ApiError) {
            if (err.statusCode === 404) throw new ApiError('User not found', 404, err.detail);
            if (err.statusCode === 429) throw new ApiError('Rate limited by Chess.com', 429, err.detail);
            if (err.statusCode === 502) throw new ApiError('Network error connecting to Chess.com', 502, err.detail);
        }
        throw err;
    }
}
