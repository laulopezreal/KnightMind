// Use the Vite dev proxy `/api` base so all frontend requests share the same origin.
// If you need a different base in the future, adjust it here rather than calling raw URLs.
export const API_BASE = '/api';

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
            // If the response is not JSON (e.g. proxy returned an HTML error page),
            // provide a clear message instead of a cryptic JSON parse error.
            if (!contentType.includes('application/json')) {
                throw new ApiError(
                    `${endpoint} returned ${response.status} (non-JSON response — backend may be unreachable)`,
                    response.status,
                    `${endpoint} → HTTP ${response.status}. The server returned HTML instead of JSON, which usually means the API is down or the proxy is misconfigured.`,
                );
            }
            const errorData = await response.json().catch(() => ({}));
            const detail = errorData.detail || response.statusText;
            throw new ApiError(`${endpoint} failed: ${detail}`, response.status, detail);
        }

        // Guard against 200 responses that are actually HTML (e.g. SPA fallback
        // served by the dev server when the proxy target is unreachable).
        if (!contentType.includes('application/json')) {
            throw new ApiError(
                `${endpoint} returned HTML instead of JSON — is the API running?`,
                502,
                `${endpoint} → received Content-Type "${contentType}". This usually means the backend is down and the dev server returned its own HTML fallback.`,
            );
        }

        return response.json();
    } catch (err) {
        if (err instanceof ApiError) throw err;
        if (err instanceof DOMException && err.name === 'AbortError') {
            throw new ApiError(`${endpoint} timed out`, 408, `${endpoint} → request timed out after ${timeout}ms.`);
        }
        // Network errors (e.g. ERR_CONNECTION_REFUSED)
        if (err instanceof TypeError) {
            throw new ApiError(
                `${endpoint} network error`,
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
