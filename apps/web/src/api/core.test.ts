import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import {
    request,
    ApiError,
    API_BASE,
    setAuthToken,
    getAuthToken,
    setUnauthorizedHandler,
} from './core';

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

describe('request()', () => {
    beforeEach(() => {
        vi.resetAllMocks();
    });

    it('should make a successful GET request', async () => {
        mockFetch.mockReturnValue(jsonResponse({ data: 'hello' }));

        const result = await request<{ data: string }>('/test');

        expect(result).toEqual({ data: 'hello' });
        expect(mockFetch).toHaveBeenCalledWith(
            `${API_BASE}/test`,
            expect.objectContaining({ signal: expect.any(AbortSignal) }),
        );
    });

    it('should throw ApiError on non-ok response', async () => {
        mockFetch.mockReturnValue(jsonResponse({ detail: 'Not found' }, 404));

        const err = await request('/missing').catch((e: unknown) => e) as ApiError;
        expect(err).toBeInstanceOf(ApiError);
        expect(err.statusCode).toBe(404);
        expect(err.detail).toBe('Not found');
        // A string backend detail is surfaced as the user-facing message.
        expect(err.message).toBe('Not found');
    });

    it('should use a friendly message when a JSON error has no detail', async () => {
        mockFetch.mockReturnValue(jsonResponse({}, 500));

        const err = await request('/nodetail').catch((e: unknown) => e) as ApiError;
        expect(err).toBeInstanceOf(ApiError);
        expect(err.statusCode).toBe(500);
        // Never blank, never the raw endpoint — a friendly generic instead.
        expect(err.message).not.toBe('');
        expect(err.message).toMatch(/try again/i);
        expect(err.message).not.toContain('/nodetail');
    });

    it('should not surface a raw 5xx detail as the user-facing message', async () => {
        mockFetch.mockReturnValue(jsonResponse({ detail: 'Internal Server Error' }, 500));

        const err = await request('/boom').catch((e: unknown) => e) as ApiError;
        expect(err.statusCode).toBe(500);
        // Friendly generic to the user…
        expect(err.message).not.toBe('Internal Server Error');
        expect(err.message).toMatch(/try again/i);
        // …but the raw cause is preserved for developer logging.
        expect(err.detail).toBe('Internal Server Error');
    });

    it('should pass through a JSON 502 detail (only bare 500 is genericised)', async () => {
        // Guards the `=== 500` boundary: a future change to `>= 500` would wrongly
        // swallow this, so pin the passthrough behaviour for other 5xx.
        mockFetch.mockReturnValue(jsonResponse({ detail: 'Could not find rating for rapid in Chess.com response' }, 502));

        const err = await request('/ratings').catch((e: unknown) => e) as ApiError;
        expect(err.statusCode).toBe(502);
        expect(err.message).toBe('Could not find rating for rapid in Chess.com response');
    });

    it('should still surface a curated 503 detail (e.g. re-import guidance)', async () => {
        mockFetch.mockReturnValue(jsonResponse(
            { detail: 'Games found but PGN content is missing. Re-import games to populate PGN data.' },
            503,
        ));

        const err = await request('/openings').catch((e: unknown) => e) as ApiError;
        expect(err.statusCode).toBe(503);
        // 503 is used deliberately for actionable guidance — do not genericise it.
        expect(err.message).toMatch(/Re-import games/);
    });

    it('should handle unparseable JSON error bodies gracefully', async () => {
        mockFetch.mockReturnValue(
            Promise.resolve({
                ok: false,
                status: 500,
                statusText: 'Internal Server Error',
                headers: new Headers({ 'content-type': 'application/json' }),
                json: () => Promise.reject(new Error('not json')),
            }),
        );

        const err = await request('/bad').catch((e: unknown) => e) as ApiError;
        expect(err).toBeInstanceOf(ApiError);
        expect(err.statusCode).toBe(500);
        expect(err.detail).toBe('Internal Server Error');
    });

    it('should flag non-JSON error responses (e.g. HTML error pages)', async () => {
        mockFetch.mockReturnValue(
            Promise.resolve({
                ok: false,
                status: 502,
                statusText: 'Bad Gateway',
                headers: new Headers({ 'content-type': 'text/html' }),
                json: () => Promise.reject(new Error('not json')),
            }),
        );

        const err = await request('/bad').catch((e: unknown) => e) as ApiError;
        expect(err).toBeInstanceOf(ApiError);
        expect(err.statusCode).toBe(502);
        // User-facing message stays friendly; technical cause lives in `detail`.
        expect(err.message).not.toContain('/bad');
        expect(err.message).toMatch(/try again/i);
        expect(err.detail).toContain('non-JSON response');
    });

    it('should flag 200 responses that are not JSON (SPA fallback)', async () => {
        mockFetch.mockReturnValue(
            Promise.resolve({
                ok: true,
                status: 200,
                statusText: 'OK',
                headers: new Headers({ 'content-type': 'text/html' }),
                json: () => Promise.reject(new Error('not json')),
            }),
        );

        const err = await request('/fallback').catch((e: unknown) => e) as ApiError;
        expect(err).toBeInstanceOf(ApiError);
        expect(err.statusCode).toBe(502);
        expect(err.message).not.toContain('/fallback');
        expect(err.message).toMatch(/try again/i);
        expect(err.detail).toContain('HTML instead of JSON');
    });

    it('should throw ApiError with status 408 on timeout', async () => {
        mockFetch.mockImplementation((_url: string, init: RequestInit) => {
            return new Promise((_resolve, reject) => {
                init.signal?.addEventListener('abort', () => {
                    reject(new DOMException('The operation was aborted.', 'AbortError'));
                });
            });
        });

        const err = await request('/slow', { timeout: 50 }).catch((e: unknown) => e) as ApiError;
        expect(err).toBeInstanceOf(ApiError);
        expect(err.statusCode).toBe(408);
        expect(err.detail).toBe('/slow → request timed out after 50ms.');
    });

    it('should respect custom timeout', async () => {
        mockFetch.mockImplementation((_url: string, init: RequestInit) => {
            return new Promise((_resolve, reject) => {
                const timer = setTimeout(() => _resolve(jsonResponse({ ok: true }).then(r => r)), 200);
                init.signal?.addEventListener('abort', () => {
                    clearTimeout(timer);
                    reject(new DOMException('The operation was aborted.', 'AbortError'));
                });
            });
        });

        // 100ms timeout should abort before the 200ms response
        await expect(request('/slow', { timeout: 100 })).rejects.toThrow(ApiError);
    });

    it('should wrap network errors in ApiError with status 0', async () => {
        mockFetch.mockRejectedValue(new TypeError('Failed to fetch'));

        const err = await request('/down').catch((e: unknown) => e) as ApiError;
        expect(err).toBeInstanceOf(ApiError);
        expect(err.statusCode).toBe(0);
        expect(err.message).not.toContain('/down');
        expect(err.message).toContain('Check your connection');
        expect(err.detail).toContain('Failed to fetch');
    });

    it('should pass through request options', async () => {
        mockFetch.mockReturnValue(jsonResponse({ ok: true }));

        await request('/post', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ key: 'value' }),
        });

        expect(mockFetch).toHaveBeenCalledWith(
            `${API_BASE}/post`,
            expect.objectContaining({
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ key: 'value' }),
            }),
        );
    });
});

describe('request() auth wiring', () => {
    beforeEach(() => {
        vi.resetAllMocks();
    });

    afterEach(() => {
        // Reset module-level auth state so tests don't leak into each other.
        setAuthToken(null);
        setUnauthorizedHandler(null);
    });

    it('attaches Authorization: Bearer when a token is set', async () => {
        setAuthToken('jwt-123');
        mockFetch.mockReturnValue(jsonResponse({ ok: true }));

        await request('/protected');

        const [, init] = mockFetch.mock.calls[0];
        // Headers is a Headers instance when a token is injected.
        const headers = init.headers as Headers;
        expect(headers.get('Authorization')).toBe('Bearer jwt-123');
    });

    it('merges the token with caller-provided headers', async () => {
        setAuthToken('jwt-abc');
        mockFetch.mockReturnValue(jsonResponse({ ok: true }));

        await request('/protected', { headers: { 'Content-Type': 'application/json' } });

        const [, init] = mockFetch.mock.calls[0];
        const headers = init.headers as Headers;
        expect(headers.get('Authorization')).toBe('Bearer jwt-abc');
        expect(headers.get('Content-Type')).toBe('application/json');
    });

    it('sends no Authorization header and leaves headers untouched with no token (backwards compat)', async () => {
        // No setAuthToken → today's behaviour: headers passed through as-is.
        mockFetch.mockReturnValue(jsonResponse({ ok: true }));

        await request('/public', { headers: { 'Content-Type': 'application/json' } });

        const [, init] = mockFetch.mock.calls[0];
        // Still the caller's plain object, not a Headers instance — unchanged.
        expect(init.headers).toEqual({ 'Content-Type': 'application/json' });
        expect(init.headers instanceof Headers).toBe(false);
    });

    it('invokes the unauthorized handler on a 401 and still throws ApiError', async () => {
        const onUnauthorized = vi.fn();
        setUnauthorizedHandler(onUnauthorized);
        mockFetch.mockReturnValue(jsonResponse({ detail: 'Invalid or missing credentials' }, 401));

        const err = await request('/protected').catch((e: unknown) => e) as ApiError;

        expect(onUnauthorized).toHaveBeenCalledTimes(1);
        expect(err).toBeInstanceOf(ApiError);
        expect(err.statusCode).toBe(401);
    });

    it('does NOT invoke the unauthorized handler for a 401 from /auth/login', async () => {
        // Bad credentials on the login exchange are shown inline, not treated as a
        // session expiry — the global logout/redirect must not fire.
        const onUnauthorized = vi.fn();
        setUnauthorizedHandler(onUnauthorized);
        mockFetch.mockReturnValue(jsonResponse({ detail: 'Invalid email or password' }, 401));

        const err = await request('/auth/login', { method: 'POST' }).catch((e: unknown) => e) as ApiError;

        expect(onUnauthorized).not.toHaveBeenCalled();
        expect(err.statusCode).toBe(401);
    });

    it('setAuthToken / getAuthToken round-trip and clear', () => {
        setAuthToken('abc');
        expect(getAuthToken()).toBe('abc');
        setAuthToken(null);
        expect(getAuthToken()).toBeNull();
    });
});
