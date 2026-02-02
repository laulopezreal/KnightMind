import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import UsernameDisplay from './UsernameDisplay';

let mockUsername = '';
const mockSetUsername = vi.fn();
let mockIsEditorOpen = false;
const mockSetEditorOpen = vi.fn();

vi.mock('../context/ChessUsernameContext', () => ({
  useChessUsername: () => ({
    username: mockUsername,
    setUsername: mockSetUsername,
    isEditorOpen: mockIsEditorOpen,
    setEditorOpen: mockSetEditorOpen,
  }),
}));

const mockValidateChessComUser = vi.fn();

vi.mock('../api', () => ({
  validateChessComUser: (...args: unknown[]) => mockValidateChessComUser(...args),
  ApiError: class extends Error { detail?: string },
}));

describe('UsernameDisplay', () => {
  const user = userEvent.setup();

  beforeEach(() => {
    vi.resetAllMocks();
    mockUsername = '';
    mockIsEditorOpen = false;
    mockValidateChessComUser.mockResolvedValue({ valid: true, username: 'player1' });
  });

  it('should show "Set Chess.com username" when no username', () => {
    render(<UsernameDisplay />);

    expect(screen.getByText('Set Chess.com username')).toBeInTheDocument();
  });

  it('should show username when set', () => {
    mockUsername = 'testplayer';
    render(<UsernameDisplay />);

    expect(screen.getByText(/testplayer/)).toBeInTheDocument();
  });

  it('should toggle editor open on button click', async () => {
    render(<UsernameDisplay />);

    await user.click(screen.getByText('Set Chess.com username'));

    expect(mockSetEditorOpen).toHaveBeenCalledWith(true);
  });

  it('should show dropdown when editor is open', () => {
    mockIsEditorOpen = true;
    render(<UsernameDisplay />);

    expect(screen.getByPlaceholderText('username')).toBeInTheDocument();
    expect(screen.getByText('Save')).toBeInTheDocument();
  });

  it('should not show dropdown when editor is closed', () => {
    mockIsEditorOpen = false;
    render(<UsernameDisplay />);

    expect(screen.queryByPlaceholderText('username')).not.toBeInTheDocument();
  });

  it('should validate and save on Save click', async () => {
    mockIsEditorOpen = true;
    render(<UsernameDisplay />);

    const input = screen.getByPlaceholderText('username');
    await user.clear(input);
    await user.type(input, 'newplayer');
    await user.click(screen.getByText('Save'));

    await waitFor(() => {
      expect(mockValidateChessComUser).toHaveBeenCalledWith('newplayer');
    });

    expect(mockSetUsername).toHaveBeenCalledWith('player1');
    expect(mockSetEditorOpen).toHaveBeenCalledWith(false);
  });

  it('should show error when validation fails', async () => {
    mockIsEditorOpen = true;
    mockValidateChessComUser.mockResolvedValue({ valid: false, error: 'User not found on Chess.com' });

    render(<UsernameDisplay />);

    const input = screen.getByPlaceholderText('username');
    await user.clear(input);
    await user.type(input, 'baduser');
    await user.click(screen.getByText('Save'));

    await waitFor(() => {
      expect(screen.getByText('User not found on Chess.com')).toBeInTheDocument();
    });
  });

  it('should show "..." while validating', async () => {
    mockIsEditorOpen = true;
    mockValidateChessComUser.mockReturnValue(new Promise(() => {}));

    render(<UsernameDisplay />);

    const input = screen.getByPlaceholderText('username');
    await user.clear(input);
    await user.type(input, 'newplayer');
    await user.click(screen.getByText('Save'));

    await waitFor(() => {
      expect(screen.getByText('...')).toBeInTheDocument();
    });
  });

  it('should close editor without saving when same username', async () => {
    mockUsername = 'existinguser';
    mockIsEditorOpen = true;

    render(<UsernameDisplay />);

    // Input should be pre-populated with existing username
    await user.click(screen.getByText('Save'));

    expect(mockValidateChessComUser).not.toHaveBeenCalled();
    expect(mockSetEditorOpen).toHaveBeenCalledWith(false);
  });
});
