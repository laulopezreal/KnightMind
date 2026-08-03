import { describe, it, expect, vi, beforeEach } from 'vitest';
import {
    getOpenings, normaliseDepth, depthLabel, DEPTH_OPTIONS, DEFAULT_MAX_PLY,
    normalisePeriod, offeredPeriod, periodParam, periodLabel, PERIOD_OPTIONS, DEFAULT_PERIOD,
} from './openings';
import { ApiError } from './core';

// Every page test mocks this module out, so nothing else connects the arguments
// callers pass to the query string the server actually reads. Renaming
// `max_ply` here left all 296 web tests green — these close that gap.

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

const TREE = {
    move_san: 'Start', ply: 0, games_count: 1, wins: 1, draws: 0, losses: 0,
    win_rate: 100, eco: null, opening_name: null, children: [],
};

/** The URL the call actually put on the wire. */
function requestedUrl(): URL {
    return new URL(mockFetch.mock.calls[0][0], 'http://localhost');
}

beforeEach(() => {
    vi.resetAllMocks();
    mockFetch.mockReturnValue(jsonResponse(TREE));
});

describe('getOpenings request', () => {
    it('sends the username and colour the caller asked for', async () => {
        await getOpenings('alice', 'black');

        const params = requestedUrl().searchParams;
        expect(params.get('username')).toBe('alice');
        expect(params.get('color')).toBe('black');
    });

    it('sends the depth as max_ply — the name the endpoint reads', async () => {
        await getOpenings('alice', 'both', 24);

        expect(requestedUrl().searchParams.get('max_ply')).toBe('24');
    });

    it('defaults to the shared default depth', async () => {
        await getOpenings('alice');

        expect(requestedUrl().searchParams.get('max_ply')).toBe(String(DEFAULT_MAX_PLY));
    });

    it('does not send min_games — the floor is the server’s to apply', async () => {
        // A client-sent floor was a cost control in the wrong place: anyone
        // holding a URL could ask for the unpruned tree.
        await getOpenings('alice', 'both', 40);

        expect(requestedUrl().searchParams.has('min_games')).toBe(false);
    });

    it('sends the window as since_days — the name the endpoint reads', async () => {
        await getOpenings('alice', 'both', 12, 90);

        expect(requestedUrl().searchParams.get('since_days')).toBe('90');
    });

    it('omits the window entirely for all time', async () => {
        // The endpoint's own default is the whole archive. A parameter meaning
        // "no filter" is one more thing that can disagree with itself.
        await getOpenings('alice', 'both', 12, null);

        expect(requestedUrl().searchParams.has('since_days')).toBe(false);
    });

    it('defaults to the whole archive', async () => {
        await getOpenings('alice');

        expect(requestedUrl().searchParams.has('since_days')).toBe(false);
    });

    it('escapes a username that would otherwise break the query', async () => {
        await getOpenings('a&b=c', 'both');

        expect(requestedUrl().searchParams.get('username')).toBe('a&b=c');
    });
});

describe('getOpenings response', () => {
    it('returns the tree on success', async () => {
        await expect(getOpenings('alice')).resolves.toEqual(TREE);
    });

    it('maps 404 to a first-run signal the page can branch on', async () => {
        // The page renders "No games imported yet" off statusCode 404; without
        // this remap that state is unreachable and users see a raw error.
        mockFetch.mockReturnValue(jsonResponse({ detail: 'No games found for user' }, 404));

        const err = await getOpenings('alice').catch((e: unknown) => e) as ApiError;
        expect(err).toBeInstanceOf(ApiError);
        expect(err.statusCode).toBe(404);
        expect(err.message).toBe('No games found');
    });

    it('passes other errors through unchanged', async () => {
        mockFetch.mockReturnValue(jsonResponse({ detail: 'Internal Server Error' }, 500));

        const err = await getOpenings('alice').catch((e: unknown) => e) as ApiError;
        expect(err.statusCode).toBe(500);
        expect(err.message).not.toBe('No games found');
    });
});

describe('depth options', () => {
    it('offers only depths the endpoint accepts', () => {
        // The endpoint declares ge=1, le=40.
        for (const plies of DEPTH_OPTIONS) {
            expect(plies).toBeGreaterThanOrEqual(1);
            expect(plies).toBeLessThanOrEqual(40);
        }
    });

    it('includes the default among the offered options', () => {
        expect(DEPTH_OPTIONS).toContain(DEFAULT_MAX_PLY);
    });

    it('labels a depth in whole moves', () => {
        expect(depthLabel(24)).toBe('12 moves');
        expect(depthLabel(8)).toBe('4 moves');
    });

    it('normalises anything not on offer back to the default', () => {
        expect(normaliseDepth(999)).toBe(DEFAULT_MAX_PLY);
        expect(normaliseDepth('12')).toBe(DEFAULT_MAX_PLY);
        expect(normaliseDepth(null)).toBe(DEFAULT_MAX_PLY);
        expect(normaliseDepth(undefined)).toBe(DEFAULT_MAX_PLY);
    });

    it('keeps a depth that is on offer', () => {
        expect(normaliseDepth(40)).toBe(40);
    });
});

describe('recency windows', () => {
    it('offers only windows the endpoint accepts', () => {
        // The endpoint declares ge=1, le=3650.
        for (const days of PERIOD_OPTIONS) {
            if (days === null) continue;
            expect(days).toBeGreaterThanOrEqual(1);
            expect(days).toBeLessThanOrEqual(3650);
        }
    });

    it('offers the whole archive as a real choice', () => {
        expect(PERIOD_OPTIONS).toContain(null);
        expect(DEFAULT_PERIOD).toBeNull();
    });

    it('labels a window in the units a person would say', () => {
        expect(periodLabel(null)).toBe('All time');
        expect(periodLabel(30)).toBe('Last 30 days');
        expect(periodLabel(365)).toBe('Last 12 months');
    });

    it('normalises anything not on offer back to the whole archive', () => {
        expect(normalisePeriod(7)).toBeNull();
        expect(normalisePeriod('90')).toBeNull();
        expect(normalisePeriod(undefined)).toBeNull();
    });

    it('keeps a window that is on offer', () => {
        expect(normalisePeriod(90)).toBe(90);
    });

    it('round-trips a window through the URL', () => {
        for (const days of PERIOD_OPTIONS) {
            expect(offeredPeriod(periodParam(days))).toBe(days);
        }
    });

    it('tells an absent window apart from an explicit all-time one', () => {
        // The whole reason this parse is three-valued: `all` is a choice whose
        // value is null, and absent must not read as that choice.
        expect(offeredPeriod(null)).toBeUndefined();
        expect(offeredPeriod('all')).toBeNull();
    });

    it('treats a window it does not offer as unnamed', () => {
        expect(offeredPeriod('7')).toBeUndefined();
        expect(offeredPeriod('banana')).toBeUndefined();
    });
});
