import { beforeEach, describe, expect, it, vi } from 'vitest';
import { API_BASE } from './core';
import { getLibraryPuzzle, getLibraryPuzzles } from './puzzles';

function assertInitialDetailIsAnswerless(
    puzzle: Awaited<ReturnType<typeof getLibraryPuzzle>>,
) {
    // @ts-expect-error Initial detail must not expose the best move.
    void puzzle.best_move_uci;
    // @ts-expect-error Initial detail must not expose accepted moves.
    void puzzle.accept_moves_uci;
    // @ts-expect-error Initial detail must not expose the solution line.
    void puzzle.solution_pv;
}

void assertInitialDetailIsAnswerless;

function assertLibraryListIsAnswerless(
    response: Awaited<ReturnType<typeof getLibraryPuzzles>>,
) {
    const puzzle = response.puzzles[0];
    // @ts-expect-error Library list rows must not expose the best move.
    void puzzle.best_move_uci;
    // @ts-expect-error Library list rows must not expose accepted moves.
    void puzzle.accept_moves_uci;
    // @ts-expect-error Library list rows must not expose the solution line.
    void puzzle.solution_pv;
}

void assertLibraryListIsAnswerless;

const mockFetch = vi.fn();
vi.stubGlobal('fetch', mockFetch);

describe('getLibraryPuzzle()', () => {
    beforeEach(() => {
        vi.resetAllMocks();
        mockFetch.mockResolvedValue({
            ok: true,
            status: 200,
            statusText: 'OK',
            headers: new Headers({ 'content-type': 'application/json' }),
            json: async () => ({
                id: 'puzzle-abc',
                fen: '8/8/8/8/8/8/8/8 w - - 0 1',
            }),
        });
    });

    it('loads detail without opting into solution fields', async () => {
        const result = await getLibraryPuzzle('puzzle/abc', 'test player');

        expect(result).not.toHaveProperty('best_move_uci');
        expect(result).not.toHaveProperty('accept_moves_uci');
        expect(mockFetch).toHaveBeenCalledWith(
            `${API_BASE}/puzzles/puzzle%2Fabc?username=test+player`,
            expect.objectContaining({ signal: expect.any(AbortSignal) }),
        );
        expect(String(mockFetch.mock.calls[0][0])).not.toContain('reveal=true');
    });
});

describe('getLibraryPuzzles()', () => {
    beforeEach(() => {
        vi.resetAllMocks();
        mockFetch.mockResolvedValue({
            ok: true,
            status: 200,
            statusText: 'OK',
            headers: new Headers({ 'content-type': 'application/json' }),
            json: async () => ({ puzzles: [], total: 0, limit: 50, offset: 0 }),
        });
    });

    it('loads list rows without opting into solution fields', async () => {
        const result = await getLibraryPuzzles({ username: 'test player' });

        expect(result.puzzles).toEqual([]);
        expect(mockFetch).toHaveBeenCalledWith(
            `${API_BASE}/puzzles/list?username=test+player`,
            expect.objectContaining({ signal: expect.any(AbortSignal) }),
        );
        expect(String(mockFetch.mock.calls[0][0])).not.toContain('reveal');
    });
});
