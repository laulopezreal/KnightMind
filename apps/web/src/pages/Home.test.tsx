import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import Home from './Home';

let mockUsername = '';

vi.mock('react-router-dom', () => ({
  Link: ({ children, to, ...props }: { children: React.ReactNode; to: string; [key: string]: unknown }) => (
    <a href={to} {...props}>{children}</a>
  ),
  useNavigate: () => vi.fn(),
}));

vi.mock('../context/ChessUsernameContext', () => ({
  useChessUsername: () => ({ username: mockUsername, setEditorOpen: vi.fn() }),
}));

vi.mock('../api', () => ({
  importChessComGames: vi.fn(),
  getImportStatus: vi.fn(),
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
  beforeEach(() => {
    vi.resetAllMocks();
    mockUsername = '';
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
});
