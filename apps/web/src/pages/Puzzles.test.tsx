import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { act, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import Puzzles from './Puzzles';
import { completeSession, generatePuzzles, getDailyPuzzles, revealPuzzle, reviewPuzzle, startSession, type JobStatusResponse } from '../api';
import { setupMockLocalStorage } from '../test/helpers';
import { WarmupSummary } from '../components/WarmupSummary';

const mockNavigate = vi.fn();
let mockSearchParams = new URLSearchParams();

let mockUsername = 'testplayer';
let mockPolledJob: JobStatusResponse | null = null;
let mockJobPollingOptions: {
  onSuccess?: (job: JobStatusResponse) => void | Promise<void>;
  onError?: (error: Error) => void;
} | undefined;

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
  useJobPolling: (_jobId: string | null, options?: typeof mockJobPollingOptions) => {
    mockJobPollingOptions = options;
    return {
      job: mockPolledJob,
      isPolling: mockPolledJob?.status === 'queued' || mockPolledJob?.status === 'running',
    };
  },
}));

const mockGetDuePuzzles = vi.fn();
const mockGetUserStatus = vi.fn();
const mockGetRecentSessions = vi.fn();
const mockGetMotifPerformance = vi.fn();
const mockGetSession = vi.fn();
const mockStartFocusPractice = vi.fn();
const mockGetTodaysFocus = vi.fn();

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
  getTodaysFocus: (...args: unknown[]) => mockGetTodaysFocus(...args),
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
    mockPolledJob = null;
    mockJobPollingOptions = undefined;
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
    mockGetTodaysFocus.mockResolvedValue({ username: 'testplayer', focus: null, below_threshold: 0, pending: 0 });
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
    // never renders. The generation-specific recovery is the observable signal.
    await screen.findByRole('button', { name: 'Try generation again' });

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

  it('shows one explanatory generation entry when imported games have no puzzles', async () => {
    mockGetUserStatus.mockResolvedValue({
      games_count: 50,
      puzzles_count: 0,
      due_count: 0,
      has_new_games: true,
    });
    vi.mocked(generatePuzzles).mockResolvedValue({ job_id: 'generation-job' });

    render(<Puzzles />);

    const generate = await screen.findByRole('button', { name: 'Generate Puzzles' });
    expect(screen.getAllByRole('button', { name: /generate/i })).toHaveLength(1);
    expect(generate).toHaveClass('min-h-11');
    expect(screen.getByText(/recent imported games.*personalized practice.*few minutes/i)).toBeInTheDocument();

    generate.focus();
    expect(generate).toHaveFocus();
  });

  it('retries a failed generation once without starting a session or duplicating rapid activation', async () => {
    mockGetUserStatus.mockResolvedValue({
      games_count: 50,
      puzzles_count: 20,
      due_count: 5,
      has_new_games: true,
    });
    vi.mocked(generatePuzzles)
      .mockRejectedValueOnce(new Error('generation failed'))
      .mockImplementationOnce(() => new Promise(() => {}));

    render(<Puzzles />);

    const generate = await screen.findByRole('button', { name: 'Generate New' });
    await waitFor(() => expect(generate).toBeEnabled());
    await act(async () => {
      generate.click();
    });

    const retry = await screen.findByRole('button', { name: 'Try generation again' });
    act(() => {
      retry.click();
      retry.click();
    });

    expect(generatePuzzles).toHaveBeenCalledTimes(2);
    expect(generatePuzzles).toHaveBeenLastCalledWith('testplayer');
    expect(startSession).not.toHaveBeenCalled();
    expect(screen.queryByRole('button', { name: 'Retry' })).not.toBeInTheDocument();
  });

  it('keeps polling and stall failures owned by generation recovery', async () => {
    mockGetUserStatus.mockResolvedValue({
      games_count: 50,
      puzzles_count: 20,
      due_count: 5,
      has_new_games: true,
    });
    mockPolledJob = {
      job_id: 'stalled-generation',
      status: 'failed',
      message: 'Generation stopped responding',
    };
    vi.mocked(generatePuzzles).mockResolvedValue({ job_id: 'retry-job' });

    render(<Puzzles />);
    act(() => mockJobPollingOptions?.onError?.(new Error('Generation stopped responding')));

    const retry = await screen.findByRole('button', { name: 'Try generation again' });
    await act(async () => {
      retry.click();
    });

    expect(generatePuzzles).toHaveBeenCalledTimes(1);
    expect(startSession).not.toHaveBeenCalled();
  });

  it('surfaces a generation-owned recovery when completed work cannot be loaded', async () => {
    mockGetUserStatus.mockResolvedValue({
      games_count: 50,
      puzzles_count: 20,
      due_count: 5,
      has_new_games: true,
    });
    vi.mocked(getDailyPuzzles).mockRejectedValueOnce(new Error('refresh unavailable'));

    render(<Puzzles />);
    await act(async () => {
      await mockJobPollingOptions?.onSuccess?.({ job_id: 'completed-job', status: 'succeeded' });
    });

    expect(await screen.findByRole('button', { name: 'Try generation again' })).toBeEnabled();
    expect(screen.queryByRole('button', { name: 'Retry session' })).not.toBeInTheDocument();
  });

  it('keeps session recovery distinct from generation recovery', async () => {
    const sessionPuzzles = {
      due_count: 1,
      returned_count: 1,
      now: new Date().toISOString(),
      puzzles: [{ id: 'p1', fen: '8/8/8/8/8/8/8/8 w - - 0 1', side_to_move: 'white', best_move_uci: 'e2e4' }],
    };
    mockGetDuePuzzles
      .mockRejectedValueOnce(new Error('session unavailable'))
      .mockResolvedValueOnce(sessionPuzzles);
    vi.mocked(startSession).mockResolvedValue({ session_id: 'session-2', requested_n: 1 } as never);

    render(<Puzzles />);

    const start = await screen.findByRole('button', { name: 'Start Session' });
    await waitFor(() => expect(start).toBeEnabled());
    await act(async () => {
      start.click();
    });

    const retry = await screen.findByRole('button', { name: 'Retry session' });
    await act(async () => {
      retry.click();
    });

    await waitFor(() => expect(mockGetDuePuzzles).toHaveBeenCalledTimes(2));
    expect(startSession).toHaveBeenCalledTimes(1);
    expect(generatePuzzles).not.toHaveBeenCalled();
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

  // The status endpoint's due_count is the broader trainable count: scheduled
  // reviews plus never-reviewed puzzles. Train must not present it as the
  // Library's narrower scheduled-due count.
  describe('Trainable versus scheduled-due copy', () => {
    it('calls the broader Train count ready to practise when no new games remain', async () => {
      // Exact scenario from issue #145: 840 games, 60 puzzles, 4 trainable,
      // no unprocessed games.
      mockGetUserStatus.mockResolvedValue({
        games_count: 840,
        puzzles_count: 60,
        due_count: 4,
        has_new_games: false,
      });

      render(<Puzzles />);

      await waitFor(() => {
        expect(
          screen.getByText(/All imported games are already processed\. You have 4 puzzles ready to practise/i)
        ).toBeInTheDocument();
      });
      expect(screen.getByRole('heading', { name: '4 puzzles ready to practise' })).toBeInTheDocument();
      expect(screen.queryByText(/4 due puzzles/i)).not.toBeInTheDocument();
      expect(screen.getByRole('button', { name: 'Start Session' })).toBeEnabled();
      // Button label should reflect state, not generic "Generate New"
      expect(screen.getByRole('button', { name: /No new games to generate/i })).toBeInTheDocument();
    });

    it('keeps existing puzzles visible without presenting dead entry actions when no review is due', async () => {
      mockGetUserStatus.mockResolvedValue({
        games_count: 840,
        puzzles_count: 60,
        due_count: 0,
        has_new_games: false,
      });

      render(<Puzzles />);

      expect(await screen.findByRole('heading', { name: 'No reviews due' })).toBeInTheDocument();
      expect(screen.getByText(/Sync newer games from Chess\.com to create more puzzles/i)).toHaveTextContent(
        'You still have 60 puzzles in your library. Sync newer games from Chess.com to create more puzzles.'
      );
      expect(screen.queryByRole('button', { name: 'Start Session' })).not.toBeInTheDocument();
      expect(screen.queryByRole('button', { name: /generate/i })).not.toBeInTheDocument();
      expect(screen.queryByText(/due puzzles/i)).not.toBeInTheDocument();
    });

    it('offers one clear existing action when new games can replenish a no-due queue', async () => {
      mockGetUserStatus.mockResolvedValue({
        games_count: 840,
        puzzles_count: 60,
        due_count: 0,
        has_new_games: true,
      });
      vi.mocked(generatePuzzles).mockResolvedValue({ job_id: 'new-puzzles-job' });

      render(<Puzzles />);

      expect(await screen.findByRole('heading', { name: 'No reviews due' })).toBeInTheDocument();
      expect(screen.queryByRole('button', { name: 'Start Session' })).not.toBeInTheDocument();
      const generate = screen.getByRole('button', { name: 'Generate from New Games' });
      expect(screen.getAllByRole('button', { name: 'Generate from New Games' })).toHaveLength(1);
      expect(generate).toBeEnabled();

      await act(async () => {
        generate.click();
      });
      expect(generatePuzzles).toHaveBeenCalledWith('testplayer');
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
      const focusPracticeMessage = screen
        .getAllByText(/server decides whether positions are safe and available/i)
        .find((message) => message.textContent === (
          'Focus practice gives you extra practice for the selected focus. The server decides whether positions are safe and available.'
        ));
      expect(focusPracticeMessage).toBeVisible();
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


    it.each([
      ['missing focus cause', 'mode=focus_practice'],
      ['empty focus cause', 'mode=focus_practice&focus_cause='],
      ['whitespace-only focus cause', 'mode=focus_practice&focus_cause=%20%20%20'],
    ])('hides dead entry actions for malformed Focus Practice with %s', async (_label, query) => {
      mockSearchParams = new URLSearchParams(query);
      mockGetUserStatus.mockResolvedValue({
        games_count: 50,
        puzzles_count: 20,
        due_count: 0,
        has_new_games: false,
      });

      render(<Puzzles />);

      expect(await screen.findByRole('heading', { name: 'No reviews due' })).toBeInTheDocument();
      expect(screen.queryByRole('button', { name: 'Start Session' })).not.toBeInTheDocument();
      expect(screen.queryByRole('button', { name: /generate/i })).not.toBeInTheDocument();
      expect(screen.getByRole('heading', { level: 1, name: 'Daily Puzzles' })).toBeInTheDocument();
      expect(screen.getByText(/Standard\s+Active/i)).toBeInTheDocument();
      expect(screen.getByText('Standard mode')).toBeInTheDocument();
      expect(mockStartFocusPractice).not.toHaveBeenCalled();
    });
  });

  describe('normal focus validation', () => {
    const focus = {
      cause: 'loose_piece_awareness',
      name: 'Loose Piece Syndrome',
      description: 'Scan loose pieces.',
      mistakes: 9,
      recent_mistakes: 4,
      accuracy: 0.4,
      priority: 12,
      rationale: '9 diagnosed mistakes.',
    };
    const duePuzzle = {
      id: 'normal-focus-puzzle',
      fen: '8/8/8/8/8/8/8/8 w - - 0 1',
      side_to_move: 'white',
      best_move_uci: 'e2e4',
    };

    it.each<[string, string, () => void]>([
      ['failed validation', 'loose_piece_awareness', () => mockGetTodaysFocus.mockRejectedValue(new Error('validation failed'))],
      ['missing focus', 'loose_piece_awareness', () => mockGetTodaysFocus.mockResolvedValue({ username: 'testplayer', focus: null, below_threshold: 0, pending: 0 })],
      ['stale mismatched focus', 'loose_piece_awareness', () => mockGetTodaysFocus.mockResolvedValue({ username: 'testplayer', focus: { ...focus, cause: 'king_safety_blindness' }, below_threshold: 0, pending: 0 })],
      ['arbitrary focus', 'arbitrary', () => mockGetTodaysFocus.mockResolvedValue({ username: 'testplayer', focus, below_threshold: 0, pending: 0 })],
      ['unauthorized response', 'loose_piece_awareness', () => mockGetTodaysFocus.mockResolvedValue({ username: 'other-player', focus, below_threshold: 0, pending: 0 })],
    ] as const)('renders ordinary Standard training and omits focus_cause for %s', async (_label, requestedCause, configure) => {
      configure();
      mockSearchParams = new URLSearchParams(`focus_cause=${requestedCause}`);
      mockGetUserStatus.mockResolvedValue({ games_count: 50, puzzles_count: 20, due_count: 1, has_new_games: false });
      mockGetDuePuzzles.mockResolvedValue({ due_count: 1, returned_count: 1, now: new Date().toISOString(), puzzles: [duePuzzle] });
      vi.mocked(startSession).mockResolvedValue({ session_id: 'normal-session', requested_n: 1 });

      render(<Puzzles />);
      const start = await screen.findByRole('button', { name: 'Start Session' });
      await waitFor(() => expect(start).toBeEnabled());
      await act(async () => start.click());

      await waitFor(() => expect(startSession).toHaveBeenCalled());
      const sessionArgs = vi.mocked(startSession).mock.calls[0];
      expect(sessionArgs[5]).not.toEqual(expect.objectContaining({ focus_cause: expect.anything() }));
      expect(screen.getByText(/Standard\s+Active/i)).toBeInTheDocument();
    });

    it('preserves a valid current focus in Standard session creation', async () => {
      mockSearchParams = new URLSearchParams('focus_cause=loose_piece_awareness');
      mockGetTodaysFocus.mockResolvedValue({ username: 'testplayer', focus, below_threshold: 0, pending: 0 });
      mockGetUserStatus.mockResolvedValue({ games_count: 50, puzzles_count: 20, due_count: 1, has_new_games: false });
      mockGetDuePuzzles.mockResolvedValue({ due_count: 1, returned_count: 1, now: new Date().toISOString(), puzzles: [duePuzzle] });
      vi.mocked(startSession).mockResolvedValue({ session_id: 'valid-focus-session', requested_n: 1 });

      render(<Puzzles />);
      const start = await screen.findByRole('button', { name: 'Start Session' });
      await waitFor(() => expect(start).toBeEnabled());
      await act(async () => start.click());

      await waitFor(() => expect(startSession).toHaveBeenCalled());
      expect(vi.mocked(startSession).mock.calls[0][5]).toEqual(expect.objectContaining({ focus_cause: focus.cause }));
    });
  });

  describe('Warmup review return', () => {
    const completedWarmup = {
      session_id: 'warmup-complete-1',
      requested_n: 5,
      pass_count: 4,
      fail_count: 1,
      total_time_ms: 60_000,
      created_at: '2025-01-15T11:59:00Z',
      completed_at: '2025-01-15T12:00:00Z',
      current_streak: 4,
      best_streak: 4,
      hints_used: 0,
      missed_puzzles: [{
        puzzle_id: 'p-abc',
        display_name: '12 Mar · Sicilian · move 18',
        cause: 'king_safety_blindness',
        cause_label: 'King safety blindness',
      }],
    };

    it('consumes the initial completion capability so a copied review URL cannot replay or resume', async () => {
      const user = userEvent.setup();
      mockSearchParams = new URLSearchParams('warmup=true');
      mockGetUserStatus.mockResolvedValue({ games_count: 50, puzzles_count: 20, due_count: 1 });
      mockGetDuePuzzles.mockResolvedValue({
        due_count: 1,
        returned_count: 1,
        now: new Date().toISOString(),
        puzzles: [{
          id: 'p-abc',
          username: 'testplayer',
          source_game_id: 'game-1',
          ply: 10,
          fen: 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1',
          side_to_move: 'white',
          eval_before: 0.5,
          eval_after: -0.5,
          swing: 1,
          created_at: '2025-01-15T11:00:00Z',
          used_on: null,
          display_name: '12 Mar · Sicilian · move 18',
        }],
      });
      vi.mocked(startSession).mockResolvedValue({ session_id: 'warmup-complete-1', requested_n: 1 });
      vi.mocked(revealPuzzle).mockResolvedValue({ best_move_uci: 'e2e4', accept_moves_uci: ['e2e4'], solution_pv: ['e2e4'] });
      vi.mocked(reviewPuzzle).mockResolvedValue({
        next_due_at: '2026-09-08T12:00:00Z',
        interval_days: 1,
        ease_factor: 2.5,
        feedback: 'Review recorded.',
        result: 'fail',
        verified: false,
        source: 'client_reported',
        puzzle_info: { fen: 'fixture', best_move: 'e2e4', side_to_move: 'white', swing: 1 },
        stats: { attempts: 1, pass_count: 0, fail_count: 1, last_reviewed_at: '2026-09-07T12:00:00Z', last_result: 'fail' },
      });
      vi.mocked(completeSession).mockResolvedValue(completedWarmup);

      const first = render(<Puzzles />);
      await user.click(await screen.findByRole('button', { name: /reveal/i }));
      await user.click(await screen.findByRole('button', { name: 'Finish Session' }));
      const reviewLink = await screen.findByRole('link', { name: 'Review 12 Mar · Sicilian · move 18' });
      const reviewUrl = reviewLink.getAttribute('href');
      const token = new URL(reviewUrl!, 'https://fixture.invalid').searchParams.get('warmup_return');
      expect(token).not.toBeNull();

      await user.click(screen.getByRole('button', { name: 'Back to Dashboard' }));
      first.unmount();
      localStorage.setItem('knightmind:session:testplayer', 'older-active-session');
      mockSearchParams = new URLSearchParams(`warmup_return=${encodeURIComponent(token!)}`);
      render(<Puzzles />);

      expect(screen.queryByRole('heading', { name: 'Warmup complete' })).not.toBeInTheDocument();
      expect(mockGetSession).not.toHaveBeenCalled();
      expect(mockGetDuePuzzles).toHaveBeenCalledTimes(1);
    });

    it('revokes an initial completion capability instead of reminting its summary across users', async () => {
      const user = userEvent.setup();
      mockSearchParams = new URLSearchParams('warmup=true');
      mockGetUserStatus.mockResolvedValue({ games_count: 50, puzzles_count: 20, due_count: 1 });
      mockGetDuePuzzles.mockResolvedValue({
        due_count: 1,
        returned_count: 1,
        now: new Date().toISOString(),
        puzzles: [{
          id: 'p-abc', username: 'testplayer', source_game_id: 'game-1', ply: 10,
          fen: 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1',
          side_to_move: 'white', eval_before: 0.5, eval_after: -0.5, swing: 1,
          created_at: '2025-01-15T11:00:00Z', used_on: null,
          display_name: '12 Mar · Sicilian · move 18',
        }],
      });
      vi.mocked(startSession).mockResolvedValue({ session_id: 'warmup-complete-1', requested_n: 1 });
      vi.mocked(revealPuzzle).mockResolvedValue({ best_move_uci: 'e2e4', accept_moves_uci: ['e2e4'], solution_pv: ['e2e4'] });
      vi.mocked(reviewPuzzle).mockResolvedValue({
        next_due_at: '2026-09-08T12:00:00Z', interval_days: 1, ease_factor: 2.5,
        feedback: 'Review recorded.', result: 'fail', verified: false, source: 'client_reported',
        puzzle_info: { fen: 'fixture', best_move: 'e2e4', side_to_move: 'white', swing: 1 },
        stats: { attempts: 1, pass_count: 0, fail_count: 1, last_reviewed_at: '2026-09-07T12:00:00Z', last_result: 'fail' },
      });
      vi.mocked(completeSession).mockResolvedValue(completedWarmup);

      const view = render(<Puzzles />);
      await user.click(await screen.findByRole('button', { name: /reveal/i }));
      await user.click(await screen.findByRole('button', { name: 'Finish Session' }));
      const reviewUrl = (await screen.findByRole('link', { name: 'Review 12 Mar · Sicilian · move 18' })).getAttribute('href');
      const token = new URL(reviewUrl!, 'https://fixture.invalid').searchParams.get('warmup_return');
      expect(token).not.toBeNull();

      mockUsername = 'other-player';
      view.rerender(<Puzzles />);

      await waitFor(() => expect(screen.queryByRole('heading', { name: 'Warmup complete' })).not.toBeInTheDocument());
      expect(WarmupSummary.readReturnToken(token, 'testplayer')).toBeNull();
      expect(screen.queryByRole('link', { name: 'Review 12 Mar · Sicilian · move 18' })).not.toBeInTheDocument();
    });

    it('shows the returned completed warmup without starting or completing another session', async () => {
      const token = WarmupSummary.createReturnToken('testplayer', completedWarmup);
      mockSearchParams = new URLSearchParams(`warmup_return=${encodeURIComponent(token!)}`);

      render(<Puzzles />);

      expect(await screen.findByRole('heading', { name: 'Warmup complete' })).toBeInTheDocument();
      expect(screen.getByText('12 Mar · Sicilian · move 18')).toBeInTheDocument();
      expect(startSession).not.toHaveBeenCalled();
      expect(completeSession).not.toHaveBeenCalled();
    });

    it('revokes a returned capability across a username round trip', async () => {
      const token = WarmupSummary.createReturnToken('testplayer', completedWarmup);
      mockSearchParams = new URLSearchParams(`warmup_return=${encodeURIComponent(token!)}`);
      const view = render(<Puzzles />);
      expect(await screen.findByRole('heading', { name: 'Warmup complete' })).toBeInTheDocument();

      mockUsername = 'other-player';
      view.rerender(<Puzzles />);
      await waitFor(() => expect(screen.queryByRole('heading', { name: 'Warmup complete' })).not.toBeInTheDocument());
      mockUsername = 'testplayer';
      view.rerender(<Puzzles />);

      expect(screen.queryByRole('heading', { name: 'Warmup complete' })).not.toBeInTheDocument();
      expect(WarmupSummary.readReturnToken(token, 'testplayer')).toBeNull();
      expect(mockGetSession).not.toHaveBeenCalled();
    });

    it('does not resume a saved active session underneath a returned completion', async () => {
      localStorage.setItem('knightmind:session:testplayer', 'older-active-session');
      const token = WarmupSummary.createReturnToken('testplayer', completedWarmup);
      mockSearchParams = new URLSearchParams(`warmup_return=${encodeURIComponent(token!)}`);

      render(<Puzzles />);

      expect(await screen.findByRole('heading', { name: 'Warmup complete' })).toBeInTheDocument();
      await waitFor(() => expect(mockGetSession).not.toHaveBeenCalled());
      expect(mockGetDuePuzzles).not.toHaveBeenCalled();
    });

    it('rejects a stale in-flight resume when return ownership arrives', async () => {
      localStorage.setItem('knightmind:session:testplayer', 'older-active-session');
      let resolveSession!: (value: Record<string, unknown>) => void;
      mockGetSession.mockReturnValue(new Promise(resolve => { resolveSession = resolve; }));

      const view = render(<Puzzles />);
      await waitFor(() => expect(mockGetSession).toHaveBeenCalledWith('older-active-session'));

      const token = WarmupSummary.createReturnToken('testplayer', completedWarmup);
      mockSearchParams = new URLSearchParams(`warmup_return=${encodeURIComponent(token!)}`);
      view.rerender(<Puzzles />);
      resolveSession({
        ...completedWarmup,
        session_id: 'older-active-session',
        completed_at: null,
        session_type: 'standard',
      });

      expect(await screen.findByRole('heading', { name: 'Warmup complete' })).toBeInTheDocument();
      await act(async () => { await Promise.resolve(); });
      expect(mockGetDuePuzzles).not.toHaveBeenCalled();
    });

    it('consumes the return token when Back to Dashboard closes the summary', async () => {
      const user = userEvent.setup();
      const token = WarmupSummary.createReturnToken('testplayer', completedWarmup);
      mockSearchParams = new URLSearchParams(`warmup_return=${encodeURIComponent(token!)}`);

      const first = render(<Puzzles />);
      await user.click(await screen.findByRole('button', { name: 'Back to Dashboard' }));
      expect(mockNavigate).toHaveBeenCalledWith('/dashboard', { replace: true });

      first.unmount();
      render(<Puzzles />);
      expect(screen.queryByRole('heading', { name: 'Warmup complete' })).not.toBeInTheDocument();
    });

    it('reuses the one return capability across repeated review navigation and denies replay after closeout', async () => {
      const user = userEvent.setup();
      localStorage.setItem('knightmind:session:testplayer', 'older-active-session');
      const token = WarmupSummary.createReturnToken('testplayer', completedWarmup);
      expect(token).not.toBeNull();
      mockSearchParams = new URLSearchParams(`warmup_return=${encodeURIComponent(token!)}`);

      const view = render(<Puzzles />);
      const firstReviewUrl = (await screen.findByRole('link', { name: 'Review 12 Mar · Sicilian · move 18' })).getAttribute('href');
      expect(firstReviewUrl).toContain(`warmup_return=${encodeURIComponent(token!)}`);

      view.rerender(<Puzzles />);
      const repeatedReviewUrl = screen.getByRole('link', { name: 'Review 12 Mar · Sicilian · move 18' }).getAttribute('href');
      expect(repeatedReviewUrl).toBe(firstReviewUrl);

      await user.click(screen.getByRole('button', { name: 'Back to Dashboard' }));
      view.unmount();
      render(<Puzzles />);

      expect(screen.queryByRole('heading', { name: 'Warmup complete' })).not.toBeInTheDocument();
      expect(startSession).not.toHaveBeenCalled();
      expect(mockGetSession).not.toHaveBeenCalled();
      expect(mockGetDuePuzzles).not.toHaveBeenCalled();
    });

    it.each([
      ['empty', ''],
      ['malformed', 'not-a-registered-token'],
      ['different-user', WarmupSummary.createReturnToken('other-player', completedWarmup)],
    ])('fails closed for %s return context without resuming stored active state', async (_label, token) => {
      localStorage.setItem('knightmind:session:testplayer', 'older-active-session');
      mockSearchParams = new URLSearchParams(`warmup_return=${encodeURIComponent(token!)}`);

      render(<Puzzles />);

      expect(screen.queryByRole('heading', { name: 'Warmup complete' })).not.toBeInTheDocument();
      await waitFor(() => expect(mockGetSession).not.toHaveBeenCalled());
      expect(mockGetDuePuzzles).not.toHaveBeenCalled();
    });

    it('fails closed for an expired return capability without resuming stored active state', async () => {
      localStorage.setItem('knightmind:session:testplayer', 'older-active-session');
      const token = WarmupSummary.createReturnToken('testplayer', {
        ...completedWarmup,
        session_id: 'expired-warmup',
      });
      expect(token).not.toBeNull();
      mockSearchParams = new URLSearchParams(`warmup_return=${encodeURIComponent(token!)}`);
      const now = Date.now();
      const dateNow = vi.spyOn(Date, 'now').mockReturnValue(now + 31 * 60 * 1000);

      try {
        render(<Puzzles />);

        expect(screen.queryByRole('heading', { name: 'Warmup complete' })).not.toBeInTheDocument();
        await waitFor(() => expect(mockGetSession).not.toHaveBeenCalled());
        expect(mockGetDuePuzzles).not.toHaveBeenCalled();
      } finally {
        dateNow.mockRestore();
      }
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
