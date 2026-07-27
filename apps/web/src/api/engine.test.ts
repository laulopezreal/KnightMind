import { describe, it, expect, vi, beforeEach } from 'vitest';
import { evaluateFen } from './engine';
import { ApiError } from './core';

const mockFetch = vi.fn();
vi.stubGlobal('fetch', mockFetch);

function jsonResponse(body: unknown, status = 200) {
    return Promise.resolve({
        ok: status >= 200 && status < 300,
        status,
        statusText: status === 200 ? 'OK' : 'Error',
        headers: new Headers({ 'content-type': 'application/json' }),
        json: () => Promise.resolve(body),
    });
}

describe('evaluateFen()', () => {
    beforeEach(() => {
        vi.resetAllMocks();
    });

    it('returns the eval result on success', async () => {
        mockFetch.mockReturnValue(jsonResponse({ best_move_uci: 'e2e4', eval: 0.3 }));

        const result = await evaluateFen('startpos');
        expect(result).toEqual({ best_move_uci: 'e2e4', eval: 0.3 });
    });

    it('maps a 503 (engine unavailable) to a friendly message, keeping the raw cause', async () => {
        // The backend 503 detail is an ops/install hint that must not reach users.
        mockFetch.mockReturnValue(jsonResponse(
            { detail: "The 'stockfish' Python package is not installed. Install it with: pip install stockfish" },
            503,
        ));

        const err = await evaluateFen('startpos').catch((e: unknown) => e) as ApiError;
        expect(err).toBeInstanceOf(ApiError);
        expect(err.statusCode).toBe(503);
        expect(err.message).toBe('The analysis engine is temporarily unavailable. Please try again later.');
        expect(err.message).not.toMatch(/stockfish|pip install|STOCKFISH_PATH/i);
        // Raw cause preserved for developer logging.
        expect(err.detail).toMatch(/pip install stockfish/);
    });

    it('passes a non-503 error through unchanged (e.g. a user-actionable 400)', async () => {
        // Only 503 is remapped; a 400 InvalidFen message must reach the user intact.
        mockFetch.mockReturnValue(jsonResponse({ detail: 'Invalid FEN string' }, 400));

        const err = await evaluateFen('not-a-fen').catch((e: unknown) => e) as ApiError;
        expect(err.statusCode).toBe(400);
        expect(err.message).toBe('Invalid FEN string');
    });

    describe('malformed 200 responses', () => {
        // A 200 does not guarantee the body matches EvalResult. Callers treat a
        // resolved promise as "these fields are the right types", and a missing
        // `eval` reached formatEval's .toFixed() and took the page down.
        const malformed: [name: string, body: unknown][] = [
            ['an empty object', {}],
            ['a missing eval', { best_move_uci: 'e2e4' }],
            ['a missing best move', { eval: 0.3 }],
            ['a stringified eval', { best_move_uci: 'e2e4', eval: '0.3' }],
            ['a null eval', { best_move_uci: 'e2e4', eval: null }],
            ['NaN', { best_move_uci: 'e2e4', eval: Number.NaN }],
            ['a non-string best move', { best_move_uci: 1234, eval: 0.3 }],
            ['null', null],
            ['an array', []],
        ];

        it.each(malformed)('rejects %s rather than resolving', async (_name, body) => {
            mockFetch.mockReturnValue(jsonResponse(body));

            const err = await evaluateFen('startpos').catch((e: unknown) => e) as ApiError;
            expect(err).toBeInstanceOf(ApiError);
            expect(err.statusCode).toBe(502);
            expect(err.message).toMatch(/unexpected response/i);
        });

        it('keeps the offending payload in detail for developers only', async () => {
            mockFetch.mockReturnValue(jsonResponse({ best_move_uci: 'e2e4' }));

            const err = await evaluateFen('startpos').catch((e: unknown) => e) as ApiError;
            expect(err.detail).toMatch(/Malformed \/engine\/eval payload/);
            expect(err.message).not.toMatch(/payload|JSON/i);
        });

        it('still accepts a legitimate zero and negative eval', async () => {
            // Guarding on truthiness rather than type would reject these.
            mockFetch.mockReturnValue(jsonResponse({ best_move_uci: 'e2e4', eval: 0 }));
            await expect(evaluateFen('startpos')).resolves.toEqual({ best_move_uci: 'e2e4', eval: 0 });

            mockFetch.mockReturnValue(jsonResponse({ best_move_uci: 'e2e4', eval: -3.5 }));
            await expect(evaluateFen('startpos')).resolves.toEqual({ best_move_uci: 'e2e4', eval: -3.5 });
        });
    });
});
