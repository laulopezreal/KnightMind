import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { act, render, screen, waitFor, within } from '@testing-library/react';
import Puzzles from './Puzzles';
import { generatePuzzles, revealPuzzle, reviewPuzzle, startSession } from '../api';
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
const mockStartFocusPractice = vi.fn();

// Puzzles.tsx imports everything from '../api' directly
vi.mock('../api', () => ({
  generatePuzzles: vi.fn(),
  getDailyPuzzles: vi.fn().mockResolvedValue([]),
  getDuePuzzles: (...args: unknown[]) => mockGetDuePuzzles(...args),
  startFocusPractice: (...args: unknown[]) => mockStartFocusPractice(...args),
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

// Sub-module mocks: forward to the barrel mock factory so vi.mocked() on
// barrel imports and impl calls on sub-modules share the same mock state.
vi.mock('../api/puzzles', async () => {
  const barrel = await vi.importMock<typeof import('../api')>('../api');
  return {
    generatePuzzles: barrel.generatePuzzles,
    getDailyPuzzles: barrel.getDailyPuzzles,
    getDuePuzzles: barrel.getDuePuzzles,
    checkPuzzle: barrel.checkPuzzle,
    revealPuzzle: barrel.revealPuzzle,
    reviewPuzzle: barrel.reviewPuzzle,
    requestMotifHint: vi.fn(),
    confirmPuzzleDiagnosis: vi.fn(),
    getPuzzleDiagnosis: vi.fn(),
    ApiError: class extends Error { detail?: string },
  };
});

vi.mock('../api/ops', async () => {
  const barrel = await vi.importMock<typeof import('../api')>('../api');
  return {
    cancelJob: barrel.cancelJob,
    getJobStatus: vi.fn(),
    reportJobStall: vi.fn(),
  };
});

vi.mock('../api/core', () => ({
  ApiError: class extends Error { detail?: string },
}));

vi.mock('../api/sessions', async () => {
  const barrel = await vi.importMock<typeof import('../api')>('../api');
  return {
    startSession: barrel.startSession,
    startFocusPractice: (...args: unknown[]) => mockStartFocusPractice(...args),
    completeSession: barrel.completeSession,
    getSession: (...args: unknown[]) => mockGetSession(...args),
    useHint: barrel.useHint,
  };
});

vi.mock('../api/users', () => ({
  getUserStatus: (...args: unknown[]) => mockGetUserStatus(...args),
  getRecentSessions: (...args: unknown[]) => mockGetRecentSessions(...args),
  getMotifPerformance: (...args: unknown[]) => mockGetMotifPerformance(...args),
  validateChessComUser: vi.fn(),
  importChessComGames: vi.fn(),
  getImportStatus: vi.fn(),
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

  // Regression tests for #411: motif ratios must be internally consistent.
  // `passed` counts passing ATTEMPTS; dividing it by `total_puzzles` rendered
  // impossible ratios like "50/16 correct — 34%" on live production data.
  describe('Weak Areas ratio arithmetic', () => {
    it('renders passed/attempts (never passed/total_puzzles) and only the weakest motifs', async () => {
      mockGetMotifPerformance.mockResolvedValue({
        motifs: [
          { name: 'pin', total_puzzles: 16, passed: 50, accuracy: 50 / 145, rank: 'needs_work', attempts: 145, insufficient_data: false },
          { name: 'fork', total_puzzles: 3, passed: 3, accuracy: 0.5, rank: 'needs_work', attempts: 6, insufficient_data: false },
          // needs_work but NOT in weakest_motifs: must not render in Weak Areas.
          { name: 'back_rank', total_puzzles: 2, passed: 2, accuracy: 0.66, rank: 'needs_work', attempts: 3, insufficient_data: true },
        ],
        weakest_motifs: ['pin', 'fork'],
      });

      render(<Puzzles />);

      // Both render sites (Weak Areas + Pattern Mastery) use passed/attempts.
      const pinRatios = await screen.findAllByText(/50\/145 attempts correct/);
      expect(pinRatios.length).toBeGreaterThanOrEqual(1);
      expect(screen.queryByText(/50\/16/)).not.toBeInTheDocument();
      expect(screen.queryByText(/3\/2\b/)).not.toBeInTheDocument();

      // Weak Areas shows only the API's reliable weakest picks; back_rank
      // (insufficient data) appears in Pattern Mastery but not as a weakness.
      const weakSection = screen.getByText('Your Weak Areas').closest('section')!;
      expect(within(weakSection).getByText('Pin')).toBeInTheDocument();
      expect(within(weakSection).getByText('Fork')).toBeInTheDocument();
      expect(within(weakSection).queryByText('Back Rank')).not.toBeInTheDocument();
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

  describe('Focus Practice session entry', () => {
    it('presents valid due-zero Focus Practice as server-owned extra practice', async () => {
      mockSearchParams = new URLSearchParams('mode=focus_practice&focus_cause=loose_piece_awareness');
      mockGetUserStatus.mockResolvedValue({
        games_count: 50,
        puzzles_count: 20,
        due_count: 0,
        has_new_games: false,
      });

      render(<Puzzles />);

      expect(await screen.findByRole('heading', { level: 1, name: 'Focus practice' })).toBeInTheDocument();
      expect(screen.getByText(/Focus practice\s+Active/i)).toBeInTheDocument();
      expect(screen.getAllByText(/extra practice for the selected focus/i)).toHaveLength(2);
      expect(screen.getAllByText(/server decides which positions are safe and available/i)).toHaveLength(2);
      expect(screen.queryByText('Daily Puzzles')).not.toBeInTheDocument();
      expect(screen.queryByText('STANDARD ACTIVE')).not.toBeInTheDocument();
      expect(screen.queryByText(/Standard mode uses spaced repetition/i)).not.toBeInTheDocument();
      expect(screen.queryByText('No puzzles are due for review yet.')).not.toBeInTheDocument();
    });

    it('enables due-zero Focus Practice and starts the dedicated server session', async () => {
      mockSearchParams = new URLSearchParams('mode=focus_practice&focus_cause=loose_piece_awareness');
      mockGetUserStatus.mockResolvedValue({
        games_count: 50,
        puzzles_count: 20,
        due_count: 0,
        has_new_games: false,
      });
      mockStartFocusPractice.mockResolvedValue({
        session_id: 'focus-zero-due',
        session_type: 'focus_practice',
        focus: { cause: 'loose_piece_awareness', name: 'Loose pieces' },
        requested_n: 5,
        returned_count: 2,
        puzzles: [
          { id: 'focus-puzzle-1', fen: '8/8/8/8/8/8/8/8 w - - 0 1', side_to_move: 'white', best_move_uci: 'e2e4' },
          { id: 'focus-puzzle-2', fen: '8/8/8/8/8/8/8/8 w - - 0 1', side_to_move: 'white', best_move_uci: 'd2d4' },
        ],
      });

      render(<Puzzles />);

      const start = await screen.findByRole('button', { name: 'Start Session' });
      await waitFor(() => expect(start).toBeEnabled());
      expect(start).toHaveClass('min-h-11');
      await act(async () => {
        start.click();
      });

      await waitFor(() => {
        expect(mockStartFocusPractice).toHaveBeenCalledWith('testplayer', 'loose_piece_awareness', 5);
      });
    });

    it('announces unchanged scheduling after a revealed Focus Practice review', async () => {
      mockSearchParams = new URLSearchParams('mode=focus_practice&focus_cause=loose_piece_awareness');
      mockGetUserStatus.mockResolvedValue({
        games_count: 50,
        puzzles_count: 20,
        due_count: 0,
        has_new_games: false,
      });
      mockStartFocusPractice.mockResolvedValue({
        session_id: 'focus-feedback-session',
        session_type: 'focus_practice',
        focus: { cause: 'loose_piece_awareness', name: 'Loose pieces' },
        requested_n: 5,
        returned_count: 2,
        puzzles: [
          { id: 'focus-feedback-puzzle', fen: '8/8/8/8/8/8/8/8 w - - 0 1', side_to_move: 'white', best_move_uci: 'e2e4' },
          { id: 'focus-feedback-puzzle-2', fen: '8/8/8/8/8/8/8/8 w - - 0 1', side_to_move: 'white', best_move_uci: 'd2d4' },
        ],
      });
      vi.mocked(reviewPuzzle).mockResolvedValue({
        result: 'fail',
        review_context: 'focus_practice',
        affects_scheduling: false,
        next_due_at: '2026-08-25T00:00:00Z',
        interval_days: 1,
        ease_factor: 2.5,
        feedback: 'Review recorded.',
        puzzle_info: { fen: '8/8/8/8/8/8/8/8 w - - 0 1', best_move: 'e2e4', side_to_move: 'white', swing: 1 },
        stats: { attempts: 1, pass_count: 0, fail_count: 1, last_reviewed_at: '2026-08-24T00:00:00Z', last_result: 'fail' },
      });
      vi.mocked(revealPuzzle).mockResolvedValue({ best_move_uci: 'e2e4', accept_moves_uci: ['e2e4'] });

      render(<Puzzles />);

      const start = await screen.findByRole('button', { name: 'Start Session' });
      await act(async () => {
        start.click();
      });
      const reveal = await screen.findByRole('button', { name: /reveal/i });
      await act(async () => {
        reveal.click();
      });

      const feedback = await screen.findByText('Practice recorded. Your normal review date is unchanged.');
      expect(feedback.closest('[role="status"]')).toHaveAttribute('aria-live', 'polite');
    });

    it('does not show Focus Practice scheduling copy after an ordinary revealed review', async () => {
      mockGetDuePuzzles.mockResolvedValue({
        due_count: 1,
        returned_count: 1,
        now: new Date().toISOString(),
        puzzles: [
          { id: 'ordinary-feedback-puzzle', fen: '8/8/8/8/8/8/8/8 w - - 0 1', side_to_move: 'white', best_move_uci: 'e2e4' },
        ],
      });
      vi.mocked(startSession).mockResolvedValue({
        session_id: 'ordinary-feedback-session',
        requested_n: 1,
      });
      vi.mocked(reviewPuzzle).mockResolvedValue({
        result: 'fail',
        next_due_at: '2026-08-25T00:00:00Z',
        interval_days: 1,
        ease_factor: 2.5,
        feedback: 'Review recorded.',
        puzzle_info: { fen: '8/8/8/8/8/8/8/8 w - - 0 1', best_move: 'e2e4', side_to_move: 'white', swing: 1 },
        stats: { attempts: 1, pass_count: 0, fail_count: 1, last_reviewed_at: '2026-08-24T00:00:00Z', last_result: 'fail' },
      });
      vi.mocked(revealPuzzle).mockResolvedValue({ best_move_uci: 'e2e4', accept_moves_uci: ['e2e4'] });
      /* The ordinary route must not inherit Focus Practice's scheduler copy. */
      render(<Puzzles />);

      const start = await screen.findByRole('button', { name: 'Start Session' });
      await act(async () => {
        start.click();
      });
      const reveal = await screen.findByRole('button', { name: /reveal/i });
      await act(async () => {
        reveal.click();
      });

      await waitFor(() => expect(reviewPuzzle).toHaveBeenCalled());
      expect(screen.queryByText('Practice recorded. Your normal review date is unchanged.')).not.toBeInTheDocument();
    });


    it('keeps ordinary due-zero sessions and malformed Focus Practice entry disabled', async () => {
      mockGetUserStatus.mockResolvedValue({
        games_count: 50,
        puzzles_count: 20,
        due_count: 0,
        has_new_games: false,
      });

      const { rerender } = render(<Puzzles />);
      expect(await screen.findByRole('button', { name: 'Start Session' })).toBeDisabled();

      mockSearchParams = new URLSearchParams('mode=focus_practice');
      rerender(<Puzzles />);
      await waitFor(() => expect(screen.getByRole('button', { name: 'Start Session' })).toBeDisabled());
      expect(screen.getByRole('heading', { level: 1, name: 'Daily Puzzles' })).toBeInTheDocument();
      expect(screen.getByText(/Standard\s+Active/i)).toBeInTheDocument();
      expect(screen.getByText('Standard mode')).toBeInTheDocument();
      expect(screen.getByText('No puzzles are due for review yet.')).toBeInTheDocument();
      expect(mockStartFocusPractice).not.toHaveBeenCalled();
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
