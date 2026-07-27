import { request, ApiError } from './core';

/**
 * Accounting for every stored game the tree did — or didn't — include.
 * Present on the root node only.
 *
 * `excluded_by_color` is what the user asked for (a colour filter), so it is
 * deliberately kept apart from `games_skipped`, which is unintended data loss
 * worth warning about.
 */
export interface OpeningAnalysis {
    /** Games held for this user in the database. */
    games_stored: number;
    /** PGNs the builder was handed. */
    games_seen: number;
    /** Games that actually reached the tree. */
    games_analyzed: number;
    excluded_by_color: number;
    /** unreadable + not_player + unfinished. */
    games_skipped: number;
    skipped_unreadable: number;
    skipped_not_player: number;
    skipped_unfinished: number;
}

export interface OpeningNode {
    move_san: string;
    ply: number;
    games_count: number;
    wins: number;
    draws: number;
    losses: number;
    /**
     * Chess score percentage — (wins + 0.5 * draws) / games — NOT the share of
     * games won. Kept under the original wire name for compatibility; always
     * surface it to users as "score". See `getScoreColor`.
     */
    win_rate: number;
    /**
     * ECO code and opening name for this position, e.g. "B90" / "Sicilian
     * Defense: Najdorf Variation". Classification is longest-prefix, so a node
     * with no entry of its own reports the most specific opening above it.
     * Null on the starting position, and for lines outside the book.
     */
    eco: string | null;
    opening_name: string | null;
    children?: OpeningNode[];
    /** Root node only. */
    analysis?: OpeningAnalysis;
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
