export const API_BASE = '/api';

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

export async function request<T>(endpoint: string, options: RequestInit = {}): Promise<T> {
    const response = await fetch(`${API_BASE}${endpoint}`, options);

    if (!response.ok) {
        const errorData = await response.json().catch(() => ({}));
        const detail = errorData.detail || response.statusText;

        // Common error handling could go here
        throw new ApiError(`Request failed: ${detail}`, response.status, detail);
    }

    return response.json();
}
