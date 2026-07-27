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
    // Model the real context: closing the editor actually closes it. A setter
    // that only recorded the call left the component permanently "open", so its
    // deferred auto-focus stayed armed and could steal focus back mid-assertion.
    mockSetEditorOpen.mockImplementation((open: boolean) => { mockIsEditorOpen = open; });
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

  it('should meet minimum 44px touch target on trigger button', () => {
    render(<UsernameDisplay />);

    const button = screen.getByRole('button');
    expect(button).toHaveClass('min-h-11');
  });

  it('associates the username input with its visible label', () => {
    mockIsEditorOpen = true;
    render(<UsernameDisplay />);

    // getByLabelText resolves the label→input link (htmlFor/id), so this passes
    // only when the input has a programmatic name (not just a placeholder).
    expect(screen.getByLabelText('Chess.com Username')).toBeInTheDocument();
  });

  it('returns focus to the trigger when the editor closes via Escape', async () => {
    mockUsername = 'testplayer';
    mockIsEditorOpen = true;
    const { rerender } = render(<UsernameDisplay />);

    const input = screen.getByLabelText('Chess.com Username');
    input.focus();
    await user.keyboard('{Escape}');

    expect(mockSetEditorOpen).toHaveBeenCalledWith(false);
    // Sit past the 100ms auto-focus timer armed on open, THEN let the close
    // reach the component as the real context would. A close that only disarms
    // that timer on re-render leaves it live across this gap: it pulls focus to
    // the input, and the re-render unmounts that input and drops focus to
    // <body>. Waiting here turns a race into a fact.
    await new Promise((r) => setTimeout(r, 150));
    rerender(<UsernameDisplay />);

    const trigger = screen.getByRole('button', { name: /chess\.com/i });
    expect(document.activeElement).toBe(trigger);
  });
});
