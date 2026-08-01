import { describe, it, expect, vi, beforeEach } from 'vitest';
import { getOpeningPractice } from './users';

// Page tests mock this module out, so nothing else connects the arguments a
// caller passes to the query string the server actually reads. Deleting a
// parameter from the wire here has passed every page test before.

const mockFetch = vi.fn();
vi.stubGlobal('fetch', mockFetch);

// Fresh object per call: returning one shared object lets React skip a state
// update and hides re-render bugs.
function jsonResponse(build: () => unknown, status = 200) {
    return Promise.resolve({
        ok: status >= 200 && status < 300,
        status,
        statusText: 'OK',
        headers: new Headers({ 'content-type': 'application/json' }),
        json: () => Promise.resolve(build()),
    });
}

const PRACTICE = () => ({
    username: 'alice',
    opening_name: 'Sicilian Defense: Najdorf Variation',
    opening_family: 'Sicilian Defense',
    line_count: 7,
    family_count: 101,
    scope: 'line' as const,
});

function requestedUrl(): URL {
    return new URL(mockFetch.mock.calls[0][0], 'http://localhost');
}

beforeEach(() => {
    vi.resetAllMocks();
    mockFetch.mockImplementation(() => jsonResponse(PRACTICE));
});

describe('getOpeningPractice request', () => {
    it('puts the username in the path and the opening on the query string', async () => {
        await getOpeningPractice('alice', 'Sicilian Defense: Najdorf Variation');

        const url = requestedUrl();
        expect(url.pathname).toBe('/api/users/alice/opening-practice');
        expect(url.searchParams.get('opening_name')).toBe(
            'Sicilian Defense: Najdorf Variation'
        );
    });

    it('sends the full line, not the family', async () => {
        // The whole point of the endpoint: the server does the split. Sending
        // "Sicilian Defense" here would silently coarsen every link.
        await getOpeningPractice('alice', 'Sicilian Defense: Najdorf Variation');
        expect(requestedUrl().searchParams.get('opening_name')).toContain(':');
    });

    it('escapes a username that would otherwise change the path', async () => {
        await getOpeningPractice('a/b', 'Bird Opening');
        expect(requestedUrl().pathname).toBe('/api/users/a%2Fb/opening-practice');
    });

    it('escapes an opening name containing an apostrophe', async () => {
        // "Queen's Gambit Declined" is one of the most common families here.
        await getOpeningPractice('alice', "Queen's Gambit Declined: Exchange Variation");
        expect(requestedUrl().searchParams.get('opening_name')).toBe(
            "Queen's Gambit Declined: Exchange Variation"
        );
    });

    it('returns the scope and both counts', async () => {
        const result = await getOpeningPractice('alice', 'Sicilian Defense: Najdorf Variation');
        expect(result.scope).toBe('line');
        expect(result.line_count).toBe(7);
        expect(result.family_count).toBe(101);
        expect(result.opening_family).toBe('Sicilian Defense');
    });
});
