import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent, act } from '@testing-library/react';
import Home from './Home';

let mockUsername = '';
const mockSetUsername = vi.fn((u: string) => { mockUsername = u; });

vi.mock('react-router-dom', () => ({
  Link: ({ children, to, ...props }: { children: React.ReactNode; to: string; [key: string]: unknown }) => (
    <a href={to} {...props}>{children}</a>
  ),
  useNavigate: () => vi.fn(),
}));

vi.mock('../context/ChessUsernameContext', () => ({
  useChessUsername: () => ({ username: mockUsername, setUsername: mockSetUsername, setEditorOpen: vi.fn() }),
}));

vi.mock('../api', () => ({
  importChessComGames: vi.fn(),
  getImportStatus: vi.fn().mockResolvedValue({ last_imported_at: null, last_new_games: null }),
  validateChessComUser: vi.fn(),
  getUserStatus: vi.fn().mockResolvedValue(null),
  ApiError: class extends Error { detail?: string },
}));

vi.mock('../api/puzzles', () => ({
  generatePuzzles: vi.fn(),
}));

vi.mock('../hooks/useJobPolling', () => ({
  useJobPolling: () => ({ job: null, isPolling: false }),
}));

vi.mock('../components/Modal', () => ({
  Modal: ({ children, isOpen }: { children: React.ReactNode; isOpen: boolean }) => isOpen ? <div>{children}</div> : null,
}));

vi.mock('../components/JobStatusCard', () => ({
  JobStatusCard: () => null,
}));

vi.mock('../components/LoadingSpinner', () => ({
  LoadingSpinner: () => <div data-testid="spinner">Loading...</div>,
}));

describe('Home', () => {
  beforeEach(async () => {
    vi.resetAllMocks();
    mockUsername = '';
    mockSetUsername.mockImplementation((u: string) => { mockUsername = u; });

    const api = await import('../api');
    (api.getImportStatus as ReturnType<typeof vi.fn>).mockResolvedValue({ last_imported_at: null, last_new_games: null });
    (api.getUserStatus as ReturnType<typeof vi.fn>).mockResolvedValue(null);
  });

  it('should render the page title', async () => {
    render(<Home />);

    await waitFor(() => {
      expect(screen.getByText('KnightMind')).toBeInTheDocument();
    });
  });

  it('should prompt user to set username when none is set', async () => {
    mockUsername = '';
    render(<Home />);

    await waitFor(() => {
      expect(screen.getByText(/Connect Chess.com Account/)).toBeInTheDocument();
    });
  });

  it('should show different content when username is set', async () => {
    mockUsername = 'testplayer';
    render(<Home />);

    await waitFor(() => {
      expect(screen.getByText('KnightMind')).toBeInTheDocument();
    });
  });

  describe('touch targets', () => {
    it('should render "Sync new games" as a bordered button (not an underline text link)', async () => {
      mockUsername = 'testplayer';
      const api = await import('../api');
      (api.getUserStatus as ReturnType<typeof vi.fn>).mockResolvedValue({
        games_count: 50,
        puzzles_count: 10,
        due_count: 3,
        has_new_games: false,
        next_due_at: null,
      });

      render(<Home />);

      const syncBtn = await screen.findByRole('button', { name: /sync new games/i });
      expect(syncBtn).toHaveClass('border');
      expect(syncBtn).not.toHaveClass('underline');
      expect(syncBtn).toHaveClass('min-h-11');
    });
  });

  describe('mobile onboarding: inline connect flow', () => {
    it('tapping Connect CTA exposes a visible input and Save control', async () => {
      mockUsername = '';
      render(<Home />);

      // Wait for the CTA to appear
      const connectBtn = await screen.findByRole('button', { name: /Connect Chess\.com Account/i });
      expect(connectBtn).toBeInTheDocument();

      // Tap the button
      fireEvent.click(connectBtn);

      // Input and Save must now be visible
      const input = screen.getByRole('textbox', { name: /Chess\.com Username/i });
      const saveBtn = screen.getByRole('button', { name: 'Save' });
      expect(input).toBeInTheDocument();
      expect(saveBtn).toBeInTheDocument();
    });

    it('saves username via /users/validate flow and shows action cards', async () => {
      const api = await import('../api');
      (api.validateChessComUser as ReturnType<typeof vi.fn>).mockResolvedValue({ valid: true, username: 'testplayer' });

      mockUsername = '';
      const { rerender } = render(<Home />);

      // Tap Connect
      const connectBtn = await screen.findByRole('button', { name: /Connect Chess\.com Account/i });
      fireEvent.click(connectBtn);

      // Fill in username
      const input = screen.getByRole('textbox', { name: /Chess\.com Username/i });
      fireEvent.change(input, { target: { value: 'testplayer' } });

      // Click Save
      const saveBtn = screen.getByRole('button', { name: 'Save' });
      await act(async () => {
        fireEvent.click(saveBtn);
      });

      // validateChessComUser should have been called
      await waitFor(() => {
        expect(api.validateChessComUser).toHaveBeenCalledWith('testplayer');
      });

      // setUsername should have been called with the validated username
      expect(mockSetUsername).toHaveBeenCalledWith('testplayer');

      // Simulate post-save state: context now has username
      mockUsername = 'testplayer';
      rerender(<Home />);

      // Action cards (Dashboard) should now appear — same post-username state as desktop
      await waitFor(() => {
        expect(screen.getByText('Dashboard')).toBeInTheDocument();
      });
    });

    it('shows validation error when username is not found', async () => {
      const api = await import('../api');
      (api.validateChessComUser as ReturnType<typeof vi.fn>).mockResolvedValue({ valid: false, error: 'User not found on Chess.com' });

      mockUsername = '';
      render(<Home />);

      const connectBtn = await screen.findByRole('button', { name: /Connect Chess\.com Account/i });
      fireEvent.click(connectBtn);

      const input = screen.getByRole('textbox', { name: /Chess\.com Username/i });
      fireEvent.change(input, { target: { value: 'unknownuser' } });

      await act(async () => {
        fireEvent.click(screen.getByRole('button', { name: 'Save' }));
      });

      await waitFor(() => {
        expect(screen.getByRole('alert')).toHaveTextContent('User not found on Chess.com');
      });

      // Editor remains open (username still not set)
      expect(screen.getByRole('textbox', { name: /Chess\.com Username/i })).toBeInTheDocument();
    });

    it('Cancel button closes the inline editor', async () => {
      mockUsername = '';
      render(<Home />);

      const connectBtn = await screen.findByRole('button', { name: /Connect Chess\.com Account/i });
      fireEvent.click(connectBtn);

      expect(screen.getByRole('textbox', { name: /Chess\.com Username/i })).toBeInTheDocument();

      fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));

      expect(screen.queryByRole('textbox', { name: /Chess\.com Username/i })).not.toBeInTheDocument();
      expect(screen.getByRole('button', { name: /Connect Chess\.com Account/i })).toBeInTheDocument();
    });
  });
});
