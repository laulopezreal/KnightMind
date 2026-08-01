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
    /**
     * The min-games floor the server actually applied — it raises a request
     * that asks for less at depth, so this is the filter that shaped the tree
     * on screen, including on a cache hit. Report this, never the request.
     */
    min_games: number;
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
export const DEPTH_OPTIONS = [8, 12, 16, 24, 40] as const;

/** "12 moves" for 24 plies — derived, so it cannot drift from the value. */
export function depthLabel(plies: number): string {
    return `${plies / 2} moves`;
}

export const DEFAULT_MAX_PLY = 12;

/** Clamp a persisted or hand-edited value onto an option we actually offer. */
export function normaliseDepth(plies: unknown): number {
    return DEPTH_OPTIONS.some(offered => offered === plies)
        ? (plies as number)
        : DEFAULT_MAX_PLY;
}

/**
 * A depth named in the URL, or null when the URL does not name a usable one.
 *
 * Deliberately *not* `normaliseDepth`: absent and invalid must both fall
 * through to the user's stored preference, and clamping them to the default
 * would silently overrule it.
 */
export function offeredDepth(raw: string | null): number | null {
    if (raw === null) return null;
    const plies = Number(raw);
    return DEPTH_OPTIONS.some(offered => offered === plies) ? plies : null;
}

/** A colour named in the URL, or null when the URL does not name a valid one. */
export function offeredColor(raw: string | null): ColorFilter | null {
    return raw === 'white' || raw === 'black' || raw === 'both' ? raw : null;
}

export async function getOpenings(
    username: string,
    color: ColorFilter = 'both',
    maxPly: number = DEFAULT_MAX_PLY
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

/** The rating band a baseline was drawn from. Null when the rating is unknown. */
export interface BaselineBand {
    low: number;
    high: number | null;
    /** e.g. "1600–1800", "2500+", "under 1000". */
    label: string;
}

export interface OpeningBaseline {
    /** How many games the figure rests on. */
    games: number;
    /**
     * Score percentage players in the band manage from this position, or null
     * when the sample is too thin to say. Null is NOT zero — render it as "no
     * comparison", never as "they score nothing here".
     */
    expected_score: number | null;
    band: BaselineBand | null;
    source: string;
}

/**
 * What players around this user's rating score from a position.
 *
 * Only white or black: under a "both" filter the user's own figure already
 * mixes games from either side of the board, so there is nothing single to
 * compare it against and the endpoint refuses.
 */
export async function getBaseline(
    username: string,
    fen: string,
    color: 'white' | 'black'
): Promise<OpeningBaseline> {
    const params = new URLSearchParams({ username, fen, color });
    return request<OpeningBaseline>(`/openings/baseline?${params}`);
}
