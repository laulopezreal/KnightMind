import { request, ApiError } from './core';

export interface OpeningNode {
    move_san: string;
    ply: number;
    games_count: number;
    wins: number;
    draws: number;
    losses: number;
    win_rate: number;
    children?: OpeningNode[];
}

export type ColorFilter = 'white' | 'black' | 'both';

export async function getOpenings(
    username: string,
    color: ColorFilter = 'both',
    maxPly: number = 12
): Promise<OpeningNode> {
    const params = new URLSearchParams({
        username,
        color,
        max_ply: maxPly.toString(),
    });

    try {
        return await request<OpeningNode>(`/openings?${params}`);
    } catch (err) {
        if (err instanceof ApiError && err.statusCode === 404) {
            throw new ApiError('No games found', 404, err.detail);
        }
        throw err;
    }
}
