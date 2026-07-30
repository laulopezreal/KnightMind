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

/**
 * Tree depth in half-moves. The API accepts 1-40; these are the choices worth
 * offering — your games go deeper than six moves, and the explorer used to
 * refuse to follow them.
 */
export const DEPTH_OPTIONS = [
    { plies: 8, label: '4 moves', minGames: 1 },
    { plies: 12, label: '6 moves', minGames: 1 },
    { plies: 16, label: '8 moves', minGames: 2 },
    { plies: 24, label: '12 moves', minGames: 2 },
    { plies: 40, label: '20 moves', minGames: 3 },
] as const;

export const DEFAULT_MAX_PLY = 12;

/**
 * How many games a line must have been played to appear, for a given depth.
 *
 * Deep trees are dominated by lines played exactly once: measured on 800 games,
 * a 40-ply tree is 96% one-off tails — 20,546 nodes and 3.8 MB of JSON, against
 * 392 nodes and 71 KB once they are dropped. Those tails are not a repertoire,
 * and rendering them is neither useful nor affordable, so depth carries a
 * matching threshold. The page states the threshold rather than filtering
 * silently.
 */
export function minGamesForDepth(plies: number): number {
    return DEPTH_OPTIONS.find(o => o.plies === plies)?.minGames ?? 1;
}

/** Clamp a persisted or hand-edited value onto an option we actually offer. */
export function normaliseDepth(plies: unknown): number {
    return DEPTH_OPTIONS.some(o => o.plies === plies)
        ? (plies as number)
        : DEFAULT_MAX_PLY;
}

export async function getOpenings(
    username: string,
    color: ColorFilter = 'both',
    maxPly: number = DEFAULT_MAX_PLY,
    minGames: number = 1
): Promise<OpeningNode> {
    const params = new URLSearchParams({
        username,
        color,
        max_ply: maxPly.toString(),
        min_games: minGames.toString(),
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
