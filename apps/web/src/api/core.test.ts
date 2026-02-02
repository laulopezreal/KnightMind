import { describe, it, expect, vi, beforeEach } from 'vitest';
import { request, ApiError, API_BASE } from './core';

const mockFetch = vi.fn();
vi.stubGlobal('fetch', mockFetch);

function jsonResponse(body: unknown, status = 200) {
    return Promise.resolve({
        ok: status >= 200 && status < 300,
        status,
        statusText: status === 200 ? 'OK' : 'Error',
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
    });

    it('should handle non-JSON error bodies gracefully', async () => {
        mockFetch.mockReturnValue(
            Promise.resolve({
                ok: false,
                status: 500,
                statusText: 'Internal Server Error',
                json: () => Promise.reject(new Error('not json')),
            }),
        );

        const err = await request('/bad').catch((e: unknown) => e) as ApiError;
        expect(err).toBeInstanceOf(ApiError);
        expect(err.statusCode).toBe(500);
        expect(err.detail).toBe('Internal Server Error');
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
        expect(err.detail).toBe('Request timed out');
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

    it('should propagate network errors', async () => {
        mockFetch.mockRejectedValue(new TypeError('Failed to fetch'));

        await expect(request('/down')).rejects.toThrow(TypeError);
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
