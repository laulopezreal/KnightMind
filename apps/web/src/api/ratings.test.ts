import { describe, it, expect, vi, beforeEach } from 'vitest';
import { getRatingExplain } from './ratings';
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

/** Minimal body carrying every container the pages dereference unconditionally. */
function explainBody(overrides: Record<string, unknown> = {}) {
    return {
        time_control: 'rapid',
        window: { start: '2026-01-01', end: '2026-02-01', source: 'games' },
        rating: { start: 1500, end: 1518, net_change: 18 },
        stats: { games: 12 },
        drivers: [],
        highlights: { best_surprises: [], worst_surprises: [] },
        confidence: 'high',
        insufficient_data: false,
        ...overrides,
    };
}

describe('getRatingExplain()', () => {
    beforeEach(() => {
        vi.resetAllMocks();
    });

    it('returns a well-formed payload unchanged', async () => {
        mockFetch.mockReturnValue(jsonResponse(explainBody()));

        const result = await getRatingExplain('someone');
        expect(result.rating.net_change).toBe(18);
        expect(result.confidence).toBe('high');
    });

    // The containers are what the render path walks without a guard; a body
    // missing one used to throw mid-render rather than at the boundary.
    it.each([
        ['rating', { rating: undefined }],
        ['stats', { stats: undefined }],
        ['window', { window: undefined }],
        ['highlights', { highlights: undefined }],
        ['drivers', { drivers: undefined }],
    ])('rejects a payload with no %s', async (_name, override) => {
        mockFetch.mockReturnValue(jsonResponse(explainBody(override)));

        const err = (await getRatingExplain('someone').catch((e: unknown) => e)) as ApiError;
        expect(err).toBeInstanceOf(ApiError);
        expect(err.statusCode).toBe(502);
        expect(err.message).toBe('Rating insights returned an unexpected response. Please try again.');
        // Raw body kept for developer logging, not shown to the user.
        expect(err.detail).toMatch(/Malformed \/ratings\/explain payload/);
    });

    it('rejects highlights that is present but not shaped like highlights', async () => {
        mockFetch.mockReturnValue(jsonResponse(explainBody({ highlights: { best_surprises: [] } })));

        const err = (await getRatingExplain('someone').catch((e: unknown) => e)) as ApiError;
        expect(err).toBeInstanceOf(ApiError);
        expect(err.statusCode).toBe(502);
    });

    it('rejects an array body, which is an object but not this object', async () => {
        mockFetch.mockReturnValue(jsonResponse([]));

        const err = (await getRatingExplain('someone').catch((e: unknown) => e)) as ApiError;
        expect(err).toBeInstanceOf(ApiError);
    });

    // Version skew is the reason this guard exists, so it must not become a
    // stricter contract than the UI needs: a payload the pages can render
    // honestly has to survive, including one whose confidence signal is absent.
    it('accepts a payload with no confidence and no chart series', async () => {
        mockFetch.mockReturnValue(
            jsonResponse(explainBody({ confidence: undefined, chart_series: undefined })),
        );

        const result = await getRatingExplain('someone');
        expect(result.confidence).toBeUndefined();
        expect(result.rating.net_change).toBe(18);
    });
});
