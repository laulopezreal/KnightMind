// Use VITE_API_BASE env var in production, fallback to /api for local dev proxy.
export const API_BASE = import.meta.env.VITE_API_BASE || '/api';

/** The actual backend URL the proxy forwards to (injected at build time). */
export const API_TARGET: string = typeof __API_TARGET__ !== 'undefined' ? __API_TARGET__ : 'unknown';

const DEFAULT_TIMEOUT_MS = 30_000;

// --- Auth wiring -------------------------------------------------------------
// The bearer token is held in a module-level variable set by the AuthProvider
// (see src/context/AuthContext.tsx). Keeping it here — rather than importing the
// auth module — avoids a circular dependency and lets every request pick up the
// current token without threading it through call sites.
//
// Backwards compatibility contract: when no token is set (the flag-off / logged-
// out state, which is today's behaviour), requests are sent EXACTLY as before —
// `fetchOptions.headers` is passed through untouched and no Authorization header
// is added. The token is only ever attached when one is present.
let authToken: string | null = null;

/** Set (or clear, with `null`) the bearer token attached to subsequent requests. */
export function setAuthToken(token: string | null): void {
    authToken = token;
}

/** Read the currently-attached bearer token (mainly for tests/diagnostics). */
export function getAuthToken(): string | null {
    return authToken;
}

// Called when a protected request comes back 401 (flag-on, missing/expired
// token). The AuthProvider registers a handler that clears the stale token and
// routes the user to /login. The failing request still rejects with an ApiError
// so the calling code can react too.
let unauthorizedHandler: (() => void) | null = null;

/** Register (or clear, with `null`) the handler invoked on a 401 response. */
export function setUnauthorizedHandler(handler: (() => void) | null): void {
    unauthorizedHandler = handler;
}

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
        // Only touch headers when a token is present, so the no-token path stays
        // byte-for-byte identical to today's requests (preserves callers that
        // pass a plain-object `headers` and the backwards-compat contract).
        let headers = fetchOptions.headers;
        if (authToken) {
            const merged = new Headers(fetchOptions.headers as HeadersInit | undefined);
            merged.set('Authorization', `Bearer ${authToken}`);
            headers = merged;
        }
        const response = await fetch(url, {
            ...fetchOptions,
            headers,
            signal: controller.signal,
        });

        const contentType = response.headers.get('content-type') ?? '';

        if (!response.ok) {
            // A 401 on any endpoint other than the login exchange itself means the
            // token is missing/expired while auth enforcement is ON. Hand off to the
            // registered handler (clears the stale token, routes to /login) before
            // rejecting. The login endpoint returns its own 401 for bad credentials,
            // which the Login page shows inline — it must not trigger a global logout.
            if (response.status === 401 && !endpoint.startsWith('/auth/login')) {
                unauthorizedHandler?.();
            }
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
            // 4xx details are written for the user ("No games found", "User not
            // found") and pass through unchanged.
            //
            // 5xx is where curated and raw text share a status code. A bare 500
            // is always an opaque wrapper, so it is always genericised. The rest
            // are mixed: the same 502 that carries "Could not find rating for
            // rapid in Chess.com response" is also raised as
            // `HTTPException(502, detail=str(e))` in several places
            // (services/api/main.py), where `str(e)` is raw Python. One observed
            // case put the full Chess.com URL and an SSL trace on the page.
            //
            // Length is what actually separates the two, so that is the test: a
            // curated 5xx message is a sentence, a connection-pool dump or stack
            // trace is not. Anything over the cap is replaced with the friendly
            // generic and kept in `detail` for the console. This is a blast
            // radius limiter, not a security boundary — secrets do not belong in
            // exception text in the first place.
            const CURATED_DETAIL_MAX = 160;
            const isOpaqueServerError = response.status === 500;
            const isOversizedServerDetail = response.status > 500
                && (backendDetail?.length ?? 0) > CURATED_DETAIL_MAX;
            const isServerError = response.status >= 500;
            const message = (!isOpaqueServerError && !isOversizedServerDetail && backendDetail)
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
