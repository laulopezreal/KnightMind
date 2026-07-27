import { request, ApiError } from './core';

export interface EvalResult {
    best_move_uci: string;
    eval: number;
}

export interface EngineStatus {
    available: boolean;
    message: string;
}

/**
 * A 200 does not guarantee the body matches `EvalResult` — a proxy, a partial
 * deploy or a mocked backend can all answer with something else. Callers treat
 * a resolved promise as "these fields are numbers and strings", and an absent
 * `eval` reached `formatEval`'s `.toFixed()` and took the whole page down via
 * the error boundary. Failing here turns that into the ordinary "evaluation
 * failed" path the page already handles.
 */
function assertEvalResult(value: unknown): EvalResult {
    const candidate = value as Partial<EvalResult> | null;
    if (
        !candidate ||
        typeof candidate.best_move_uci !== 'string' ||
        typeof candidate.eval !== 'number' ||
        !Number.isFinite(candidate.eval)
    ) {
        throw new ApiError(
            'The analysis engine returned an unexpected response. Please try again.',
            502,
            `Malformed /engine/eval payload: ${JSON.stringify(value)?.slice(0, 200)}`,
        );
    }
    return candidate as EvalResult;
}

export async function evaluateFen(fen: string): Promise<EvalResult> {
    try {
        return assertEvalResult(
            await request<EvalResult>('/engine/eval', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ fen }),
            }),
        );
    } catch (err) {
        // /engine/eval returns 503 when Stockfish is unavailable, and its detail is
        // a developer/ops hint (e.g. "pip install stockfish", set STOCKFISH_PATH).
        // Show users a friendly message while keeping the raw cause in `detail`.
        if (err instanceof ApiError && err.statusCode === 503) {
            throw new ApiError(
                'The analysis engine is temporarily unavailable. Please try again later.',
                503,
                err.detail,
            );
        }
        throw err;
    }
}

export async function getEngineStatus(): Promise<EngineStatus> {
    return await request<EngineStatus>('/engine/status');
}
