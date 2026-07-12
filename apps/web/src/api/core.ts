// Use VITE_API_BASE env var in production, fallback to /api for local dev proxy.
export const API_BASE = import.meta.env.VITE_API_BASE || '/api';

/** The actual backend URL the proxy forwards to (injected at build time). */
export const API_TARGET: string = typeof __API_TARGET__ !== 'undefined' ? __API_TARGET__ : 'unknown';

const DEFAULT_TIMEOUT_MS = 30_000;

export class ApiError extends Error {
    statusCode: number;
    detail?: string;

    constructor(message: string, statusCode: number, detail?: string) {
        super(message);
        this.name = 'ApiError';
        this.statusCode = statusCode;
        this.detail = detail;
    }
}

export interface RequestOptions extends RequestInit {
    timeout?: number;
}

export async function request<T>(endpoint: string, options: RequestOptions = {}): Promise<T> {
    const { timeout = DEFAULT_TIMEOUT_MS, ...fetchOptions } = options;

    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), timeout);

    // Allow callers to pass their own signal; if they do, forward aborts to our controller.
    const callerSignal = fetchOptions.signal;
    const abortListener = () => controller.abort();
    if (callerSignal) {
        callerSignal.addEventListener('abort', abortListener);
    }

    try {
        const url = `${API_BASE}${endpoint}`;
        const response = await fetch(url, {
            ...fetchOptions,
            signal: controller.signal,
        });

        const contentType = response.headers.get('content-type') ?? '';

        if (!response.ok) {
            // `message` is user-facing and must stay friendly; the technical cause
            // (endpoint, status, content-type) goes into `detail` for debugging.
            if (!contentType.includes('application/json')) {
                throw new ApiError(
                    'Something went wrong on our end. Please try again in a moment.',
                    response.status,
                    `${endpoint} returned ${response.status} (non-JSON response — backend may be unreachable).`,
                );
            }
            const errorData = await response.json().catch(() => ({}));
            // Only a string `detail` from the backend is safe to show as the
            // user-facing message. Otherwise fall back to a friendly generic —
            // never surface a raw statusText, an empty string, or a non-string
            // detail (e.g. FastAPI 422 arrays) as the message.
            const backendDetail = typeof errorData.detail === 'string' ? errorData.detail : undefined;
            const message = backendDetail || 'Something went wrong. Please try again in a moment.';
            const detail = backendDetail || response.statusText || `HTTP ${response.status}`;
            throw new ApiError(message, response.status, detail);
        }

        // Guard against 200 responses that are actually HTML (e.g. SPA fallback
        // served by the dev server when the proxy target is unreachable).
        if (!contentType.includes('application/json')) {
            throw new ApiError(
                "We couldn't reach the server. Please try again.",
                502,
                `${endpoint} returned HTML instead of JSON (Content-Type "${contentType}") — the backend is likely down.`,
            );
        }

        return response.json();
    } catch (err) {
        if (err instanceof ApiError) throw err;
        if (err instanceof DOMException && err.name === 'AbortError') {
            throw new ApiError('The request timed out. Please try again.', 408, `${endpoint} → request timed out after ${timeout}ms.`);
        }
        // Network errors (e.g. ERR_CONNECTION_REFUSED)
        if (err instanceof TypeError) {
            throw new ApiError(
                "Can't reach the server. Check your connection and try again.",
                0,
                `${endpoint} → ${err.message}. Check that the API server is running.`,
            );
        }
        throw err;
    } finally {
        clearTimeout(timeoutId);
        if (callerSignal) {
            callerSignal.removeEventListener('abort', abortListener);
        }
    }
}
