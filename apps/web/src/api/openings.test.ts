import { describe, it, expect, vi, beforeEach } from 'vitest';
import { getOpenings, normaliseDepth, depthLabel, DEPTH_OPTIONS, DEFAULT_MAX_PLY } from './openings';
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
