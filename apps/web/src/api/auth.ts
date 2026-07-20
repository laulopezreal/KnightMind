import { request } from './core';

// Mirrors services/api/auth_routes.py.

/** Response from POST /auth/login. */
export interface LoginResponse {
    access_token: string;
    token_type: string;
}

/** Response from GET /auth/me — the authenticated account and its claimed handles. */
export interface Account {
    id: string;
    email: string;
    usernames: string[];
}

/** Exchange email + password for a signed JWT bearer token. */
export async function login(email: string, password: string): Promise<LoginResponse> {
    return request<LoginResponse>('/auth/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email, password }),
    });
}

/** Return the account for the currently-attached bearer token (401 if invalid). */
export async function me(): Promise<Account> {
    return request<Account>('/auth/me');
}
