import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Routes, Route, useLocation } from 'react-router-dom';
import { AuthProvider, useAuth, AUTH_TOKEN_KEY } from './AuthContext';
import { setupMockLocalStorage } from '../test/helpers';
import { request, setAuthToken, getAuthToken } from '../api/core';

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

// Route fetch responses by URL so a single mock serves login + me + protected.
function installHappyPath() {
    mockFetch.mockImplementation((url: string) => {
        if (url.includes('/auth/login')) {
            return jsonResponse({ access_token: 'jwt-tok', token_type: 'bearer' });
        }
        if (url.includes('/auth/me')) {
            return jsonResponse({ id: 'acc-1', email: 'player@example.com', usernames: ['magnus'] });
        }
        return jsonResponse({ ok: true });
    });
}

function LocationProbe() {
    const location = useLocation();
    return <span data-testid="path">{location.pathname}</span>;
}

function Consumer() {
    const { isAuthenticated, account, login, logout } = useAuth();
    return (
        <div>
            <span data-testid="authed">{String(isAuthenticated)}</span>
            <span data-testid="email">{account?.email ?? ''}</span>
            <button onClick={() => login('player@example.com', 'pw').catch(() => {})}>login</button>
            <button onClick={() => logout()}>logout</button>
            <button onClick={() => { request('/protected').catch(() => {}); }}>call-protected</button>
        </div>
    );
}

function renderApp(initialPath = '/') {
    return render(
        <MemoryRouter initialEntries={[initialPath]}>
            <AuthProvider>
                <LocationProbe />
                <Routes>
                    <Route path="*" element={<Consumer />} />
                </Routes>
            </AuthProvider>
        </MemoryRouter>,
    );
}

describe('AuthContext', () => {
    const user = userEvent.setup();

    beforeEach(() => {
        setupMockLocalStorage();
        setAuthToken(null);
        mockFetch.mockReset();
        installHappyPath();
    });

    it('starts unauthenticated with no stored token', async () => {
        renderApp();
        await waitFor(() => expect(screen.getByTestId('authed')).toHaveTextContent('false'));
        expect(getAuthToken()).toBeNull();
    });

    it('login stores the token and attaches Authorization on the follow-up /auth/me call', async () => {
        renderApp();

        await user.click(screen.getByText('login'));

        await waitFor(() => expect(screen.getByTestId('authed')).toHaveTextContent('true'));
        expect(screen.getByTestId('email')).toHaveTextContent('player@example.com');
        expect(localStorage.getItem(AUTH_TOKEN_KEY)).toBe('jwt-tok');
        expect(getAuthToken()).toBe('jwt-tok');

        // The /auth/me request issued right after login must carry the bearer token.
        const meCall = mockFetch.mock.calls.find(([url]) => String(url).includes('/auth/me'));
        expect(meCall).toBeTruthy();
        const headers = meCall![1].headers as Headers;
        expect(headers.get('Authorization')).toBe('Bearer jwt-tok');
    });

    it('validates an existing stored token on mount', async () => {
        localStorage.setItem(AUTH_TOKEN_KEY, 'stored-jwt');
        renderApp();

        await waitFor(() => expect(screen.getByTestId('email')).toHaveTextContent('player@example.com'));
        expect(screen.getByTestId('authed')).toHaveTextContent('true');
    });

    it('logout clears the token (storage + API layer) and routes to /login', async () => {
        localStorage.setItem(AUTH_TOKEN_KEY, 'stored-jwt');
        renderApp();
        await waitFor(() => expect(screen.getByTestId('authed')).toHaveTextContent('true'));

        await user.click(screen.getByText('logout'));

        await waitFor(() => expect(screen.getByTestId('path')).toHaveTextContent('/login'));
        expect(localStorage.getItem(AUTH_TOKEN_KEY)).toBeNull();
        expect(getAuthToken()).toBeNull();
        expect(screen.getByTestId('authed')).toHaveTextContent('false');
    });

    it('a 401 on a protected call clears the token and routes to /login', async () => {
        localStorage.setItem(AUTH_TOKEN_KEY, 'stored-jwt');
        renderApp();
        await waitFor(() => expect(screen.getByTestId('authed')).toHaveTextContent('true'));

        // Next protected request returns 401 (expired/invalid token, flag on).
        mockFetch.mockImplementation((url: string) => {
            if (url.includes('/protected')) {
                return jsonResponse({ detail: 'Invalid or missing credentials' }, 401);
            }
            return jsonResponse({ ok: true });
        });

        await user.click(screen.getByText('call-protected'));

        await waitFor(() => expect(screen.getByTestId('path')).toHaveTextContent('/login'));
        expect(localStorage.getItem(AUTH_TOKEN_KEY)).toBeNull();
        expect(getAuthToken()).toBeNull();
    });
});
