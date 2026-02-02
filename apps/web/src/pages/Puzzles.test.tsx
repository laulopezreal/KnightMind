import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import Puzzles from './Puzzles';
import { setupMockLocalStorage } from '../test/helpers';

const mockNavigate = vi.fn();
let mockSearchParams = new URLSearchParams();

let mockUsername = 'testplayer';

vi.mock('react-router-dom', () => ({
  useNavigate: () => mockNavigate,
  useSearchParams: () => [mockSearchParams, vi.fn()],
  Link: ({ children, to, ...props }: { children: React.ReactNode; to: string; [key: string]: unknown }) => (
    <a href={to} {...props}>{children}</a>
  ),
}));

vi.mock('../context/ChessUsernameContext', () => ({
  useChessUsername: () => ({ username: mockUsername, setEditorOpen: vi.fn() }),
}));

vi.mock('../context/PuzzleModeContext', () => ({
  usePuzzleMode: () => ({
    sessionType: 'standard',
    sessionCount: 10,
    timeoutSeconds: 80,
  }),
}));

vi.mock('../hooks/useJobPolling', () => ({
  useJobPolling: () => ({ job: null, isPolling: false }),
}));

const mockGetDuePuzzles = vi.fn();
const mockGetUserStatus = vi.fn();
const mockGetRecentSessions = vi.fn();
const mockGetMotifPerformance = vi.fn();

// Puzzles.tsx imports everything from '../api' directly
vi.mock('../api', () => ({
  generatePuzzles: vi.fn(),
  getDailyPuzzles: vi.fn().mockResolvedValue([]),
  getDuePuzzles: (...args: unknown[]) => mockGetDuePuzzles(...args),
  startSession: vi.fn(),
  completeSession: vi.fn(),
  reviewPuzzle: vi.fn(),
  getSession: vi.fn().mockRejectedValue(new Error('No session')),
  useHint: vi.fn(),
  getUserStatus: (...args: unknown[]) => mockGetUserStatus(...args),
  getRecentSessions: (...args: unknown[]) => mockGetRecentSessions(...args),
  getMotifPerformance: (...args: unknown[]) => mockGetMotifPerformance(...args),
  cancelJob: vi.fn(),
  ApiError: class extends Error { detail?: string },
}));

vi.mock('../components/JobStatusCard', () => ({
  JobStatusCard: () => null,
}));

vi.mock('../components/SessionSummaryCard', () => ({
  SessionSummaryCard: () => <div data-testid="session-summary">Summary</div>,
}));

vi.mock('../components/WarmupSummary', () => ({
  WarmupSummary: () => <div data-testid="warmup-summary">WarmupSummary</div>,
}));

vi.mock('../components/AchievementsList', () => ({
  AchievementsList: () => null,
}));

vi.mock('../components/RecentSessionsCard', () => ({
  RecentSessionsCard: () => null,
}));

vi.mock('react-chessboard', () => ({
  Chessboard: () => <div data-testid="chessboard">Chessboard</div>,
}));

vi.mock('chess.js', () => {
  class MockChess {
    load = vi.fn();
    move = vi.fn();
    fen = vi.fn().mockReturnValue('rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1');
    get = vi.fn().mockReturnValue(null);
    turn = vi.fn().mockReturnValue('w');
    board = vi.fn().mockReturnValue([]);
  }
  return { Chess: MockChess };
});

describe('Puzzles', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    setupMockLocalStorage();
    mockUsername = 'testplayer';
    mockSearchParams = new URLSearchParams();
    mockGetUserStatus.mockResolvedValue({
      games_count: 50,
      puzzles_count: 20,
      due_count: 5,
    });
    mockGetDuePuzzles.mockResolvedValue([]);
    mockGetRecentSessions.mockResolvedValue([]);
    mockGetMotifPerformance.mockResolvedValue({ motifs: [], weakest_motifs: [] });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('should show prompt when no username', async () => {
    mockUsername = '';

    render(<Puzzles />);

    await waitFor(() => {
      expect(screen.getByText(/Set your Chess.com username/i)).toBeInTheDocument();
    });
  });

  it('should render page heading', async () => {
    render(<Puzzles />);

    await waitFor(() => {
      expect(screen.getByText('Daily Puzzles')).toBeInTheDocument();
    });
  });

  it('should show back to dashboard link', () => {
    render(<Puzzles />);

    expect(screen.getByText(/Back to Dashboard/)).toBeInTheDocument();
  });

  it('should show no-games message when user has no games', async () => {
    mockGetUserStatus.mockResolvedValue({
      games_count: 0,
      puzzles_count: 0,
      due_count: 0,
    });

    render(<Puzzles />);

    await waitFor(() => {
      expect(screen.getByText(/no games imported/i)).toBeInTheDocument();
    });
  });
});
