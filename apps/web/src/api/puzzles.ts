import { request, ApiError } from './core';

export interface ManualPuzzlePayload {
    username: string;
    fen: string;
    title: string;
    motif: string;
    source?: string;
    solution_pv: string;
}

export interface ManualPuzzleResult {
    puzzle_id: string;
    is_new: boolean;
}

export interface Puzzle {
    id: string;
    username: string;
    source_game_id: string;
    ply: number;
    fen: string;
    side_to_move: string;
    // Solution-revealing fields. Omitted from the SCORED training payloads
    // (/puzzles/due, /daily-puzzle-sessions) so the answer can't be pre-read
    // before an attempt (audit gate 13). The Train board checks moves via
    // checkPuzzle() and fetches the solution via revealPuzzle().
    played_move_uci?: string;
    best_move_uci?: string;
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
    // Server-decided outcome and whether it was independently verified from the
    // played move. `source` is "server_verified" or "client_reported"; a
    // self-reported pass (verified === false) must not be shown as verified.
    result?: 'pass' | 'fail';
    verified?: boolean;
    source?: 'server_verified' | 'client_reported' | null;
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
    try {
        return await request<DailyPuzzlesResponse>(`/daily-puzzle-sessions`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                username,
                n,
            }),
        });
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
    targetAccuracy?: number,
    motif?: string
): Promise<DuePuzzlesResponse> {
    const params = new URLSearchParams({
        username,
        n: n.toString(),
        session_type: sessionType,
    });

    if (targetAccuracy !== undefined) {
        params.append('target_accuracy', targetAccuracy.toString());
    }

    if (motif) {
        params.append('motif', motif);
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

// --- Library (Puzzle Exploration) ---

export type PuzzleStatus = 'new' | 'due' | 'learning' | 'mastered';
export type PuzzleDifficulty = 'easy' | 'medium' | 'hard';
export type PuzzleSort = 'due_soonest' | 'last_attempted' | 'most_failed' | 'difficulty_asc' | 'difficulty_desc' | 'newest';

export interface PuzzleDiagnosisSummary {
    state: 'ready' | 'unclear' | 'unavailable';
    primary_cause: string | null;
    primary_cause_label: string | null;
    source: string | null;
    diagnosed_at: string | null;
}

export interface LibraryPuzzle {
    id: string;
    title: string | null;
    primary_motif: string | null;
    difficulty: PuzzleDifficulty;
    swing: number;
    fen: string;
    side_to_move: string;
    best_move_uci: string;
    status: PuzzleStatus;
    attempts: number;
    pass_count: number;
    fail_count: number;
    last_reviewed_at: string | null;
    last_result: string | null;
    next_due_at: string | null;
    created_at: string | null;
    diagnosis_summary: PuzzleDiagnosisSummary | null;
}

export interface LibraryCorpusStats {
    total: number;
    due: number;
    new: number;
    learning: number;
    mastered: number;
}

export interface LibraryListResponse {
    puzzles: LibraryPuzzle[];
    total: number;
    limit: number;
    offset: number;
    available_motifs: string[];
    stats: LibraryCorpusStats;
}

export interface LibraryListParams {
    username: string;
    q?: string;
    status?: PuzzleStatus;
    motif?: string;
    difficulty?: PuzzleDifficulty;
    sort?: PuzzleSort;
    limit?: number;
    offset?: number;
}

export async function getLibraryPuzzle(
    puzzleId: string,
    username: string
): Promise<LibraryPuzzle> {
    // The detail page checks/reveals the move client-side, so it opts in to the
    // solution with reveal=true. The list surface never asks for it (dim 13).
    const params = new URLSearchParams({ username, reveal: 'true' });
    return await request<LibraryPuzzle>(`/puzzles/${encodeURIComponent(puzzleId)}?${params}`);
}

export async function getLibraryPuzzles(
    params: LibraryListParams
): Promise<LibraryListResponse> {
    const searchParams = new URLSearchParams({ username: params.username });

    if (params.q) searchParams.append('q', params.q);
    if (params.status) searchParams.append('status', params.status);
    if (params.motif) searchParams.append('motif', params.motif);
    if (params.difficulty) searchParams.append('difficulty', params.difficulty);
    if (params.sort) searchParams.append('sort', params.sort);
    if (params.limit !== undefined) searchParams.append('limit', params.limit.toString());
    if (params.offset !== undefined) searchParams.append('offset', params.offset.toString());

    return await request<LibraryListResponse>(`/puzzles/list?${searchParams}`);
}

// --- Training board: server-side check + explicit reveal (audit gate 13) ---

export interface CheckPuzzleResponse {
    correct: boolean;
    result: 'pass' | 'fail';
    // For a full-PV puzzle, the opponent's forced reply to a correct move (the
    // next line ply). Safe to auto-play — it is the forced response, never the
    // solver's upcoming answer, which the server never sends. null for a wrong
    // move, a legacy single-move puzzle, or the final ply of the line.
    reply?: string | null;
    // True once the whole line is solved (or, for a legacy puzzle, on the one
    // correct move) — record the verified pass at this point.
    complete?: boolean;
    // The solver's next move index in the line (this ply + 2). null when done.
    next_ply_index?: number | null;
}

export interface RevealPuzzleResponse {
    best_move_uci: string;
    accept_moves_uci: string[];
    // The full solution line (UCI). Empty for legacy single-move puzzles; the
    // first move always equals best_move_uci.
    solution_pv?: string[];
}

/**
 * Verify a played move server-side for live board feedback WITHOUT revealing the
 * solution. The response is only correct/incorrect — the answer never travels to
 * the client. Recording/scheduling still goes through reviewPuzzle().
 */
export async function checkPuzzle(
    puzzleId: string,
    username: string,
    attemptedMove: string,
    // Index of this move within the solution line (an even ply). Defaults to 0
    // so single-move puzzles keep working with a bare call.
    plyIndex: number = 0
): Promise<CheckPuzzleResponse> {
    return await request<CheckPuzzleResponse>(
        `/puzzles/${encodeURIComponent(puzzleId)}/check`,
        {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ username, attempted_move: attemptedMove, ply_index: plyIndex }),
        }
    );
}

/**
 * Explicitly fetch a puzzle's solution (the "give up / show me" path). The
 * scored training payload no longer carries the answer, so the board asks for it
 * here on demand.
 */
export async function revealPuzzle(
    puzzleId: string,
    username: string
): Promise<RevealPuzzleResponse> {
    return await request<RevealPuzzleResponse>(
        `/puzzles/${encodeURIComponent(puzzleId)}/reveal`,
        {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ username }),
        }
    );
}

export async function reviewPuzzle(
    puzzleId: string,
    username: string,
    result: 'pass' | 'fail',
    timeSpentMs?: number,
    sessionId?: string,
    clientReviewId?: string,
    attemptedMove?: string
): Promise<ReviewPuzzleResponse> {
    return await request<ReviewPuzzleResponse>(`/puzzles/${puzzleId}/review`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            username,
            result,
            time_spent_ms: timeSpentMs,
            session_id: sessionId,
            // Idempotency key: lets the server dedupe a retried/double-submitted
            // review so a double-click or network retry can't double-count.
            client_review_id: clientReviewId,
            // The UCI move actually played. When present, the SERVER verifies it
            // and decides pass/fail — the client no longer self-grades. Omitted
            // for no-move outcomes (timeout, "mark failed", revealed solution).
            attempted_move: attemptedMove,
        }),
    });
}

export async function createManualPuzzle(
    payload: ManualPuzzlePayload
): Promise<ManualPuzzleResult> {
    return await request<ManualPuzzleResult>('/puzzles/manual', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
    });
}

// --- Mistake diagnosis ---

export type DiagnosisState = 'ready' | 'unclear' | 'pending' | 'unavailable';

export interface DiagnosisEvidenceItem {
    id: string;
    label: string;
    value: string;
}

export interface PuzzleDiagnosis {
    state: DiagnosisState;
    puzzle_id: string;
    primary_motif?: string | null;
    primary_cause?: string | null;
    primary_cause_label?: string | null;
    secondary_causes: string[];
    secondary_cause_labels: string[];
    phase?: string | null;
    evidence: DiagnosisEvidenceItem[];
    evidence_withheld: boolean;
    explanation?: string | null;
    training_recommendation?: string | null;
    user_confirmed_cause?: string | null;
    source?: string | null;
    diagnosed_at?: string | null;
}

/**
 * Read the stored diagnosis for a puzzle. Never computes one server-side, so a
 * missing diagnosis comes back as `pending` rather than blocking the response.
 *
 * `reveal` is opt-in because the evidence names the solution move. Callers must
 * only pass it once the puzzle has been attempted or revealed.
 */
export async function getPuzzleDiagnosis(
    puzzleId: string,
    username: string,
    reveal = false
): Promise<PuzzleDiagnosis> {
    const params = new URLSearchParams({ username });
    if (reveal) params.append('reveal', 'true');
    return await request<PuzzleDiagnosis>(
        `/puzzles/${encodeURIComponent(puzzleId)}/diagnosis?${params}`
    );
}
