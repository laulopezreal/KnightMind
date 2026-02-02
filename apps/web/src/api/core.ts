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
        const response = await fetch(`${API_BASE}${endpoint}`, {
            ...fetchOptions,
            signal: controller.signal,
        });

        if (!response.ok) {
            const errorData = await response.json().catch(() => ({}));
            const detail = errorData.detail || response.statusText;

            // Common error handling could go here
            throw new ApiError(`Request failed: ${detail}`, response.status, detail);
        }

        return response.json();
    } catch (err) {
        if (err instanceof ApiError) throw err;
        if (err instanceof DOMException && err.name === 'AbortError') {
            throw new ApiError('Request timed out', 408, 'Request timed out');
        }
        throw err;
    } finally {
        clearTimeout(timeoutId);
        if (callerSignal) {
            callerSignal.removeEventListener('abort', abortListener);
        }
    }
}
