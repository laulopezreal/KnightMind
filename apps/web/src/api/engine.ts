import { request } from './core';

export interface EvalResult {
    best_move_uci: string;
    eval: number;
}

export interface EngineStatus {
    available: boolean;
    message: string;
}

export async function evaluateFen(fen: string): Promise<EvalResult> {
    return await request<EvalResult>('/engine/eval', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ fen }),
    });
}

export async function getEngineStatus(): Promise<EngineStatus> {
    return await request<EngineStatus>('/engine/status');
}
