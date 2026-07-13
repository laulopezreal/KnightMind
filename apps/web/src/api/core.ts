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
            // Most backend details are user-actionable and safe to display: 4xx
            // ("No games found", "User not found") and some deliberate 5xx — e.g.
            // /openings raises 503 "Re-import games to populate PGN data". The one
            // detail we suppress here is a bare 500 (explicit HTTPException(500),
            // whose detail is an opaque "Internal server error: ..." wrapper) —
            // it blames nothing the user can act on. Show a friendly generic
            // there, keeping the raw detail in `detail` for logging. Scoped to
            // 500 (not >= 500) so curated 503s still reach the user.
            // NOTE: some other 5xx still pass their detail through unchanged
            // (e.g. /engine/eval 503 surfaces a Stockfish install hint) — those
            // are pre-existing and out of scope here; friendlying them is a
            // separate follow-up.
            const isOpaqueServerError = response.status === 500;
            const isServerError = response.status >= 500;
            const message = (!isOpaqueServerError && backendDetail)
                ? backendDetail
                // "on our end" only blames the server for actual 5xx; a detail-less
                // 4xx (e.g. a bare 400) is a client-side issue, so stay neutral.
                : isServerError
                    ? 'Something went wrong on our end. Please try again in a moment.'
                    : 'Something went wrong. Please try again in a moment.';
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
