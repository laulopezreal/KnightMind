import { request, ApiError } from './core';

export interface EvalResult {
    best_move_uci: string;
    eval: number;
}

export interface EngineStatus {
    available: boolean;
    message: string;
}

export async function evaluateFen(fen: string): Promise<EvalResult> {
    try {
        return await request<EvalResult>('/engine/eval', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ fen }),
        });
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
