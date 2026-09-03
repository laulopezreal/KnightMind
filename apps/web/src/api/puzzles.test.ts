import { beforeEach, describe, expect, it, vi } from 'vitest';
import { API_BASE } from './core';
import { getLibraryPuzzle } from './puzzles';

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
