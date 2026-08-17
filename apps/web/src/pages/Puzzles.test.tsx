import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import Puzzles from './Puzzles';
import { generatePuzzles } from '../api';
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
const mockGetSession = vi.fn();

// Puzzles.tsx imports everything from '../api' directly
vi.mock('../api', () => ({
  generatePuzzles: vi.fn(),
  getDailyPuzzles: vi.fn().mockResolvedValue([]),
  getDuePuzzles: (...args: unknown[]) => mockGetDuePuzzles(...args),
  startSession: vi.fn(),
  completeSession: vi.fn(),
  reviewPuzzle: vi.fn(),
  checkPuzzle: vi.fn().mockResolvedValue({ correct: true, result: 'pass' }),
  revealPuzzle: vi.fn().mockResolvedValue({ best_move_uci: 'e2e4', accept_moves_uci: ['e2e4'] }),
  getSession: (...args: unknown[]) => mockGetSession(...args),
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
    vi.resetAllMocks();
    setupMockLocalStorage();
    mockUsername = 'testplayer';
    mockSearchParams = new URLSearchParams();
    mockGetUserStatus.mockResolvedValue({
      games_count: 50,
      puzzles_count: 20,
      due_count: 5,
    });
    mockGetDuePuzzles.mockResolvedValue({ due_count: 0, returned_count: 0, now: new Date().toISOString(), puzzles: [] });
    mockGetRecentSessions.mockResolvedValue([]);
    mockGetMotifPerformance.mockResolvedValue({ motifs: [], weakest_motifs: [] });
    mockGetSession.mockRejectedValue(new Error('No session'));
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('should show prompt when no username', async () => {
    mockUsername = '';

    render(<Puzzles />);

    // The shared connect state, same as every other account-dependent page.
    expect(
      await screen.findByRole('heading', { level: 2, name: /connect your chess\.com account/i })
    ).toBeInTheDocument();
    expect(screen.getByText(/generated out of your own games/i)).toBeInTheDocument();
  });

  it('offers a working route to connect an account, not a dead button', async () => {
    mockUsername = '';

    render(<Puzzles />);

    // Was a "Set Username" button calling setEditorOpen. That editor lives in
    // UsernameDisplay, which Layout only mounts once a username exists — so it
    // did nothing in the one state that rendered it. Home's onboarding is the
    // only way in, so this must be a real navigation.
    expect(
      await screen.findByRole('button', { name: /connect account/i })
    ).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Set Username/i })).not.toBeInTheDocument();
  });

  it('shows the shared connect state, not a disabled training console', async () => {
    // Every other account-dependent page swaps to ConnectAccountEmpty in place.
    // Puzzles used to render its whole console with every control dead and an
    // inline sentence explaining why.
    mockUsername = '';

    render(<Puzzles />);

    expect(
      await screen.findByRole('heading', { level: 2, name: /connect your chess\.com account/i })
    ).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Start Session' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Generate New/i })).not.toBeInTheDocument();
  });

  it('does not stack a second connect control on the error card', async () => {
    // Reachable only by a narrow path: generate with an account, hit an error,
    // then clear the account. The error card used to add its own "Connect
    // account" button — a second control to the same place, worded differently,
    // beside the one the page already offers.
    mockUsername = 'testplayer';
    mockGetUserStatus.mockResolvedValue({
      games_count: 50,
      puzzles_count: 20,
      due_count: 5,
      has_new_games: true,
    });
    vi.mocked(generatePuzzles).mockRejectedValue(new Error('generation blew up'));

    const { rerender } = render(<Puzzles />);

    const generate = await screen.findByRole('button', { name: /Generate New/i });
    await waitFor(() => expect(generate).toBeEnabled());
    generate.click();

    // JobStatusCard is stubbed to null in this suite, so the error text itself
    // never renders — the card's own Retry button is the observable signal.
    await screen.findByRole('button', { name: 'Retry' });

    // Now the account goes away underneath the error state.
    mockUsername = '';
    rerender(<Puzzles />);

    // Exactly one way to connect, singular on purpose so a second reappearing
    // fails here.
    expect(
      await screen.findAllByRole('button', { name: /connect account/i })
    ).toHaveLength(1);
    expect(screen.queryByRole('button', { name: /Set Username/i })).not.toBeInTheDocument();
  });

  it('states the connect message once, not twice', async () => {
    mockUsername = '';

    render(<Puzzles />);

    await screen.findByRole('button', { name: /connect account/i });

    // Exactly one heading, not the old panel message stacked beside a
    // near-identical disabled-control explanation. The description repeating
    // the phrase is the house pattern — every page's copy ends that way — so
    // the contract is on the heading, not on raw text occurrences.
    expect(
      screen.getAllByRole('heading', { name: /connect your chess\.com account/i })
    ).toHaveLength(1);
    expect(screen.queryByText(/Set your username first/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/to start training\./i)).not.toBeInTheDocument();
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

  // Regression test for issue #145: games imported + puzzles exist + generation unavailable
  describe('Generate disabled state copy', () => {
    it('shows context-aware copy and button label when games imported, puzzles exist, and no new games', async () => {
      // Exact scenario from issue #145: 840 games, 60 puzzles, 4 due, no unprocessed games
      mockGetUserStatus.mockResolvedValue({
        games_count: 840,
        puzzles_count: 60,
        due_count: 4,
        has_new_games: false,
      });

      render(<Puzzles />);

      await waitFor(() => {
        // Visible helper text should guide user to train due puzzles
        expect(
          screen.getByText(/All imported games are already processed\. Train your 4 due puzzles/i)
        ).toBeInTheDocument();
      });
      // Button label should reflect state, not generic "Generate New"
      expect(screen.getByRole('button', { name: /No new games to generate/i })).toBeInTheDocument();
    });

    it('sets button title to sync-only message when no due puzzles remain', async () => {
      // When due_count=0, startSessionDisabledReason takes the <p> slot;
      // verify the generate reason is still surfaced as the button tooltip
      mockGetUserStatus.mockResolvedValue({
        games_count: 840,
        puzzles_count: 60,
        due_count: 0,
        has_new_games: false,
      });

      render(<Puzzles />);

      await waitFor(() => {
        const btn = screen.getByRole('button', { name: /No new games to generate/i });
        expect(btn).toHaveAttribute('title', expect.stringContaining('Sync newer games from Chess.com to generate more puzzles'));
      });
    });
  });

  describe('Session Resume', () => {
    const mockActiveSession = {
      session_id: 'test-session-123',
      requested_n: 5,
      pass_count: 2,
      fail_count: 1,
      total_time_ms: 60000,
      created_at: '2025-01-01T00:00:00Z',
      completed_at: null,
      session_type: 'standard',
      current_streak: 1,
      best_streak: 2,
      hints_used: 0,
    };

    const mockPuzzles = [
      {
        id: 'puzzle-1',
        username: 'testplayer',
        source_game_id: 'game-1',
        ply: 10,
        fen: 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1',
        side_to_move: 'w',
        played_move_uci: 'e2e3',
        best_move_uci: 'e2e4',
        eval_before: 0.5,
        eval_after: -0.5,
        swing: 1.0,
        created_at: '2025-01-01T00:00:00Z',
        display_name: 'Test Puzzle', used_on: null,
      },
    ];

    it('should call getSession when localStorage has saved session', async () => {
      localStorage.setItem('knightmind:session:testplayer', 'test-session-123');
      mockGetSession.mockResolvedValue(mockActiveSession);
      mockGetDuePuzzles.mockResolvedValue({
        due_count: 1, returned_count: 1, now: new Date().toISOString(), puzzles: mockPuzzles,
      });

      render(<Puzzles />);

      await waitFor(() => {
        expect(mockGetSession).toHaveBeenCalledWith('test-session-123');
      });

      // Wait for full render to settle so effects don't fire after cleanup
      await waitFor(() => {
        expect(screen.getByText('Session in Progress')).toBeInTheDocument();
      });
    });

    it('should show session progress after resuming active session', async () => {
      localStorage.setItem('knightmind:session:testplayer', 'test-session-123');
      mockGetSession.mockResolvedValue(mockActiveSession);
      mockGetDuePuzzles.mockResolvedValue({
        due_count: 1, returned_count: 1, now: new Date().toISOString(), puzzles: mockPuzzles,
      });

      render(<Puzzles />);

      await waitFor(() => {
        expect(screen.getByText('Session in Progress')).toBeInTheDocument();
      });
      // Two counters are rendered — the desktop panel and the compact mobile
      // strip — with CSS (not conditional rendering) choosing which is visible,
      // so both are in the DOM under jsdom.
      expect(screen.getAllByText('3 / 5')).toHaveLength(2);
      expect(
        within(screen.getByTestId('mobile-session-progress')).getByText('3 / 5'),
      ).toBeInTheDocument();

      // Wait for session-state persistence effect to flush before cleanup
      await waitFor(() => {
        expect(localStorage.getItem('knightmind:sessionState:testplayer')).not.toBeNull();
      });
    });

    it('should clear localStorage when saved session is already completed', async () => {
      localStorage.setItem('knightmind:session:testplayer', 'completed-session');
      mockGetSession.mockResolvedValue({
        ...mockActiveSession,
        session_id: 'completed-session',
        completed_at: '2025-01-01T01:00:00Z',
      });

      render(<Puzzles />);

      await waitFor(() => {
        expect(mockGetSession).toHaveBeenCalledWith('completed-session');
      });

      await waitFor(() => {
        expect(localStorage.getItem('knightmind:session:testplayer')).toBeNull();
      });
    });

    it('should clear localStorage when getSession fails', async () => {
      localStorage.setItem('knightmind:session:testplayer', 'bad-session');
      mockGetSession.mockRejectedValue(new Error('Session not found'));

      render(<Puzzles />);

      await waitFor(() => {
        expect(mockGetSession).toHaveBeenCalledWith('bad-session');
      });

      await waitFor(() => {
        expect(localStorage.getItem('knightmind:session:testplayer')).toBeNull();
      });
    });

    it('should restore streak from localStorage session state', async () => {
      localStorage.setItem('knightmind:session:testplayer', 'test-session-123');
      localStorage.setItem('knightmind:sessionState:testplayer', JSON.stringify({
        sessionId: 'test-session-123',
        currentIndex: 0,
        streak: 3,
        performanceHistory: [],
      }));
      mockGetSession.mockResolvedValue(mockActiveSession);
      mockGetDuePuzzles.mockResolvedValue({
        due_count: 1, returned_count: 1, now: new Date().toISOString(), puzzles: mockPuzzles,
      });

      render(<Puzzles />);

      await waitFor(() => {
        expect(screen.getByText('Session in Progress')).toBeInTheDocument();
      });
      // Stats are a mono figure above a small-caps label, not "Streak: 3" prose.
      const streak = screen.getByTestId('session-stat-streak');
      expect(within(streak).getByText('3')).toBeInTheDocument();
      expect(within(streak).getByText('Streak')).toBeInTheDocument();
    });
  });

  describe('Session Timer', () => {
    const mockActiveSession = {
      session_id: 'timed-session-1',
      requested_n: 5,
      pass_count: 0,
      fail_count: 0,
      total_time_ms: 0,
      created_at: '2025-01-01T00:00:00Z',
      completed_at: null,
      session_type: 'standard',
      current_streak: 0,
      best_streak: 0,
      hints_used: 0,
    };

    const mockPuzzles = [
      {
        id: 'puzzle-1',
        username: 'testplayer',
        source_game_id: 'game-1',
        ply: 10,
        fen: 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1',
        side_to_move: 'w',
        played_move_uci: 'e2e3',
        best_move_uci: 'e2e4',
        eval_before: 0.5,
        eval_after: -0.5,
        swing: 1.0,
        created_at: '2025-01-01T00:00:00Z',
        display_name: 'Test Puzzle', used_on: null,
      },
      {
        id: 'puzzle-2',
        username: 'testplayer',
        source_game_id: 'game-2',
        ply: 20,
        fen: 'rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1',
        side_to_move: 'b',
        played_move_uci: 'd7d6',
        best_move_uci: 'd7d5',
        eval_before: -0.3,
        eval_after: 0.5,
        swing: 0.8,
        created_at: '2025-01-01T00:00:00Z',
        display_name: 'Test Puzzle', used_on: null,
      },
    ];

    it('should display chessboard when session has puzzles', async () => {
      localStorage.setItem('knightmind:session:testplayer', 'timed-session-1');
      mockGetSession.mockResolvedValue(mockActiveSession);
      mockGetDuePuzzles.mockResolvedValue({
        due_count: 2, returned_count: 2, now: new Date().toISOString(), puzzles: mockPuzzles,
      });

      render(<Puzzles />);

      await waitFor(() => {
        expect(screen.getByTestId('chessboard')).toBeInTheDocument();
      });
    });

    it('should show hints counter in session stats', async () => {
      localStorage.setItem('knightmind:session:testplayer', 'timed-session-1');
      mockGetSession.mockResolvedValue({ ...mockActiveSession, hints_used: 2 });
      mockGetDuePuzzles.mockResolvedValue({
        due_count: 2, returned_count: 2, now: new Date().toISOString(), puzzles: mockPuzzles,
      });

      render(<Puzzles />);

      await waitFor(() => {
        const hints = screen.getByTestId('session-stat-hints');
        expect(within(hints).getByText('2')).toBeInTheDocument();
        expect(within(hints).getByText('Hints')).toBeInTheDocument();
      });
    });

    it('should show progress counter during active session', async () => {
      localStorage.setItem('knightmind:session:testplayer', 'timed-session-1');
      mockGetSession.mockResolvedValue({ ...mockActiveSession, pass_count: 1, fail_count: 1 });
      mockGetDuePuzzles.mockResolvedValue({
        due_count: 2, returned_count: 2, now: new Date().toISOString(), puzzles: mockPuzzles,
      });

      render(<Puzzles />);

      await waitFor(() => {
        expect(screen.getAllByText('2 / 5').length).toBeGreaterThan(0);
      });
    });
  });
});
