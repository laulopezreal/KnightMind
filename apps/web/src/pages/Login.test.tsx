import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import Login from './Login';
import { ApiError } from '../api/core';

const mockLogin = vi.fn();

vi.mock('../context/AuthContext', () => ({
    useAuth: () => ({ login: mockLogin, isAuthenticated: false }),
}));

function renderLogin() {
    return render(
        <MemoryRouter>
            <Login />
        </MemoryRouter>,
    );
}

describe('Login page', () => {
    const user = userEvent.setup();

    beforeEach(() => {
        mockLogin.mockReset();
    });

    it('renders an accessible form (labelled email + password, submit button)', () => {
        renderLogin();
        // getByLabelText proves each input is programmatically associated with a label.
        expect(screen.getByLabelText('Email')).toBeInTheDocument();
        expect(screen.getByLabelText('Password')).toBeInTheDocument();
        expect(screen.getByRole('button', { name: /sign in/i })).toBeInTheDocument();
    });

    it('submits trimmed credentials to login()', async () => {
        mockLogin.mockResolvedValue(undefined);
        renderLogin();

        await user.type(screen.getByLabelText('Email'), '  player@example.com  ');
        await user.type(screen.getByLabelText('Password'), 'secret-pw');
        await user.click(screen.getByRole('button', { name: /sign in/i }));

        await waitFor(() => expect(mockLogin).toHaveBeenCalledWith('player@example.com', 'secret-pw'));
    });

    it('announces a failed login via role="alert"', async () => {
        mockLogin.mockRejectedValue(new ApiError('Invalid email or password', 401));
        renderLogin();

        await user.type(screen.getByLabelText('Email'), 'player@example.com');
        await user.type(screen.getByLabelText('Password'), 'wrong');
        await user.click(screen.getByRole('button', { name: /sign in/i }));

        const alert = await screen.findByRole('alert');
        expect(alert).toHaveTextContent(/invalid email or password/i);
    });

    it('validates empty fields before calling login(), announced via role="alert"', async () => {
        renderLogin();

        await user.click(screen.getByRole('button', { name: /sign in/i }));

        const alert = await screen.findByRole('alert');
        expect(alert).toHaveTextContent(/enter your email and password/i);
        expect(mockLogin).not.toHaveBeenCalled();
    });
});
