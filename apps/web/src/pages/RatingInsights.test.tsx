import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import RatingInsights from './RatingInsights';
import { setupMockLocalStorage } from '../test/helpers';

const mockNavigate = vi.fn();
let mockUsername = 'testplayer';
const mockSetEditorOpen = vi.fn();

vi.mock('react-router-dom', () => ({
  useNavigate: () => mockNavigate,
}));

vi.mock('../context/ChessUsernameContext', () => ({
  useChessUsername: () => ({
    username: mockUsername,
    setEditorOpen: mockSetEditorOpen,
  }),
}));

const mockGetRatingExplain = vi.fn();
const mockGetRatingHistory = vi.fn();
const mockCreateSnapshot = vi.fn();
const mockGetRecentSessions = vi.fn();

vi.mock('../api/ratings', () => ({
  getRatingExplain: (...args: unknown[]) => mockGetRatingExplain(...args),
  getRatingHistory: (...args: unknown[]) => mockGetRatingHistory(...args),
  createSnapshot: (...args: unknown[]) => mockCreateSnapshot(...args),
}));

vi.mock('../api/sessions', () => ({
  getRecentSessions: (...args: unknown[]) => mockGetRecentSessions(...args),
}));

// Mock recharts
vi.mock('recharts', () => ({
  LineChart: ({ children }: { children: React.ReactNode }) => <div data-testid="line-chart">{children}</div>,
  Line: () => <div />,
  XAxis: () => <div />,
  YAxis: () => <div />,
  Tooltip: () => <div />,
  ResponsiveContainer: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  ReferenceDot: () => <div />,
}));

describe('RatingInsights', () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setupMockLocalStorage();
    mockUsername = 'testplayer';
    mockGetRatingExplain.mockRejectedValue(new Error('Not loaded'));
    mockGetRatingHistory.mockRejectedValue(new Error('Not loaded'));
    mockGetRecentSessions.mockResolvedValue([]);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('should show a connect-account prompt when no username', () => {
    mockUsername = '';
    render(<RatingInsights />);

    expect(screen.getByText('Connect your Chess.com account')).toBeInTheDocument();
    // The old prompt's button called setEditorOpen, but that editor is not
    // mounted while the username is empty — so it could never do anything.
    expect(screen.queryByText('Set Username')).not.toBeInTheDocument();
    expect(mockSetEditorOpen).not.toHaveBeenCalled();
  });

  it('should render page heading', () => {
    mockGetRatingExplain.mockReturnValue(new Promise(() => {}));
    mockGetRatingHistory.mockReturnValue(new Promise(() => {}));

    render(<RatingInsights />);

    expect(screen.getByText('Rating Insights')).toBeInTheDocument();
  });

  it('should show loading state', async () => {
    mockGetRatingExplain.mockReturnValue(new Promise(() => {}));
    mockGetRatingHistory.mockReturnValue(new Promise(() => {}));

    render(<RatingInsights />);

    await waitFor(() => {
      expect(screen.getByText('Analyzing games...')).toBeInTheDocument();
    });
  });

  it('shows the main loading state during the sessions probe (phase 1), not a blank page', async () => {
    // Sessions request still pending: the main area must already show the
    // loading state instead of rendering nothing until phase 2 begins.
    mockGetRecentSessions.mockReturnValue(new Promise(() => {}));
    mockGetRatingExplain.mockReturnValue(new Promise(() => {}));
    mockGetRatingHistory.mockReturnValue(new Promise(() => {}));

    render(<RatingInsights />);

    await waitFor(() => {
      expect(screen.getByText('Analyzing games...')).toBeInTheDocument();
    });
  });

  it('should only call getRatingExplain once on mount (no double-fetch)', async () => {
    mockGetRatingExplain.mockResolvedValue({
      rating: { start: null, end: null, net_change: null, reference_rating: 0, reference_is_approx: false },
      stats: { games: 0, wins: 0, draws: 0, losses: 0, actual_minus_expected: null, avg_opponent_rating: null, missing_opponent_rating_games: 0 },
      drivers: [],
      highlights: { best_surprises: [], worst_surprises: [] },
      window: null,
    });
    mockGetRatingHistory.mockResolvedValue([]);

    render(<RatingInsights />);

    await waitFor(() => {
      expect(mockGetRatingExplain).toHaveBeenCalledTimes(1);
    });

    // Wait extra to ensure no second call happens
    await new Promise(r => setTimeout(r, 100));
    expect(mockGetRatingExplain).toHaveBeenCalledTimes(1);
    // Zero sessions triggers the auto-switch to Last 7 Days; the effect re-run
    // it causes must reuse the first probe's result, not hit sessions again.
    expect(mockGetRecentSessions).toHaveBeenCalledTimes(1);
  });

  it('should show error when API fails', async () => {
    mockGetRatingExplain.mockRejectedValue(new Error('Server error'));
    mockGetRatingHistory.mockRejectedValue(new Error('Server error'));

    render(<RatingInsights />);

    await waitFor(() => {
      expect(screen.getByText('Server error')).toBeInTheDocument();
    });
  });

  it('should show import onboarding (no manual snapshot ask) when no games', async () => {
    mockGetRatingExplain.mockResolvedValue({
      rating: { start: null, end: null, net_change: null, reference_rating: 0, reference_is_approx: false },
      stats: { games: 0, wins: 0, draws: 0, losses: 0, actual_minus_expected: null, avg_opponent_rating: null, missing_opponent_rating_games: 0 },
      drivers: [],
      highlights: { best_surprises: [], worst_surprises: [] },
      window: null,
    });
    mockGetRatingHistory.mockResolvedValue([]);

    render(<RatingInsights />);

    await waitFor(() => {
      expect(screen.getByText(/No Rapid games yet/i)).toBeInTheDocument();
      expect(screen.getByText('Import your games')).toBeInTheDocument();
      expect(screen.getByText(/Nothing to record by hand/i)).toBeInTheDocument();
    });
    // Snapshots are automatic: the manual button must never come back.
    expect(screen.queryByText(/Record Snapshot/i)).not.toBeInTheDocument();
  });

  it('shows the chart + window-insufficient note (not first-snapshot onboarding) when snapshots exist but the window is thin', async () => {
    // Repro: snapshots on file (history non-empty) but the selected window has 0 games,
    // so the explain payload reports rating.end === null / insufficient_data.
    mockGetRatingExplain.mockResolvedValue({
      rating: { start: null, end: null, net_change: null, reference_rating: 0, reference_is_approx: false },
      stats: { games: 0, wins: 0, draws: 0, losses: 0, actual_minus_expected: null, avg_opponent_rating: null, missing_opponent_rating_games: 0 },
      drivers: [],
      highlights: { best_surprises: [], worst_surprises: [] },
      window: null,
      confidence: 'low',
      insufficient_data: true,
    });
    mockGetRatingHistory.mockResolvedValue([
      { rating: 1200, recorded_at: '2025-01-01T00:00:00Z' },
      { rating: 1240, recorded_at: '2025-01-05T00:00:00Z' },
    ]);

    render(<RatingInsights />);

    await waitFor(() => {
      expect(screen.getByText(/Not enough games in this window/i)).toBeInTheDocument();
    });
    // The recorded snapshot history still renders as a chart.
    expect(screen.getByTestId('line-chart')).toBeInTheDocument();
    // Crucially, the brand-new-user onboarding must NOT appear.
    expect(screen.queryByText(/Step 1/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Step 2/)).not.toBeInTheDocument();
  });

  it('should show time control buttons', () => {
    mockGetRatingExplain.mockReturnValue(new Promise(() => {}));
    mockGetRatingHistory.mockReturnValue(new Promise(() => {}));

    render(<RatingInsights />);

    expect(screen.getByText('Bullet')).toBeInTheDocument();
    expect(screen.getByText('Blitz')).toBeInTheDocument();
    expect(screen.getByText('Rapid')).toBeInTheDocument();
  });

  it('should not offer a manual Record Snapshot button', () => {
    mockGetRatingExplain.mockReturnValue(new Promise(() => {}));
    mockGetRatingHistory.mockReturnValue(new Promise(() => {}));

    render(<RatingInsights />);

    expect(screen.queryByText(/Record Snapshot/i)).not.toBeInTheDocument();
  });

  it('should show window selectors', () => {
    mockGetRatingExplain.mockReturnValue(new Promise(() => {}));
    mockGetRatingHistory.mockReturnValue(new Promise(() => {}));

    render(<RatingInsights />);

    expect(screen.getByText('Since Session')).toBeInTheDocument();
    expect(screen.getByText('Last 7 Days')).toBeInTheDocument();
  });

  it('should show summary cards when games exist', async () => {
    mockGetRatingExplain.mockResolvedValue({
      rating: { start: 1200, end: 1250, net_change: 50, reference_rating: 1220, reference_is_approx: false },
      stats: { games: 25, wins: 15, draws: 3, losses: 7, actual_minus_expected: 2.5, avg_opponent_rating: 1230, missing_opponent_rating_games: 0 },
      drivers: [{ text: 'Strong wins vs higher-rated', direction: 'up', severity: 'major' }],
      highlights: { best_surprises: [], worst_surprises: [] },
      window: { start: '2025-01-01T00:00:00Z', end: '2025-01-15T00:00:00Z' },
    });
    mockGetRatingHistory.mockResolvedValue([]);

    render(<RatingInsights />);

    await waitFor(() => {
      expect(screen.getByText('+50')).toBeInTheDocument();
      expect(screen.getByText('15W - 3D - 7L')).toBeInTheDocument();
      expect(screen.getByText('25 games analyzed')).toBeInTheDocument();
    });
  });

  it('charts the per-game trajectory (no snapshots needed) and flags estimated net change', async () => {
    mockGetRatingExplain.mockResolvedValue({
      rating: { start: 1400, end: 1432, net_change: 32, is_estimated: true, reference_rating: 1410, reference_is_approx: true },
      stats: { games: 5, wins: 4, draws: 0, losses: 1, actual_minus_expected: 1.2, avg_opponent_rating: 1420, missing_opponent_rating_games: 0, casual_games_excluded: 2 },
      drivers: [],
      highlights: { best_surprises: [], worst_surprises: [] },
      window: { start: '2025-01-01T00:00:00Z', end: '2025-01-15T00:00:00Z' },
      trajectory: [
        { played_at: '2025-01-02T10:00:00Z', rating: 1400 },
        { played_at: '2025-01-03T10:00:00Z', rating: 1410 },
        { played_at: '2025-01-04T10:00:00Z', rating: 1432 },
      ],
      confidence: 'low',
      insufficient_data: false,
    });
    mockGetRatingHistory.mockResolvedValue([]);

    render(<RatingInsights />);

    await waitFor(() => {
      // Chart renders from game trajectory even with zero recorded snapshots.
      expect(screen.getByTestId('line-chart')).toBeInTheDocument();
      expect(screen.getByText(/From your games in this window/i)).toBeInTheDocument();
      // Net change renders. This legacy payload has only the conflated
      // is_estimated flag, so BOTH anchors are annotated — the exact old
      // combined note, not a per-anchor variant.
      expect(screen.getByText('+32')).toBeInTheDocument();
      expect(screen.getByText(/1400 → 1432 \(est\. from games\)/)).toBeInTheDocument();
      expect(screen.queryByText(/start est\./)).not.toBeInTheDocument();
      expect(screen.queryByText(/end est\./)).not.toBeInTheDocument();
      // Casual games are surfaced as excluded from attribution.
      expect(screen.getByText(/2 casual games excluded/i)).toBeInTheDocument();
    });
  });

  it('renders the server-fused chart series so the line ends on the card end anchor', async () => {
    // Mixed case: fresh snapshot (1455) won the end anchor over the stale
    // last-game Elo (1440). The chart must render chart_series — ending at
    // 1455 — not the raw trajectory, so card and chart agree.
    mockGetRatingExplain.mockResolvedValue({
      rating: {
        start: 1420, end: 1455, net_change: 35,
        is_estimated: true, start_is_estimated: true, end_is_estimated: false,
        reference_rating: 1430, reference_is_approx: false,
      },
      stats: { games: 3, wins: 2, draws: 0, losses: 1, actual_minus_expected: 0.8, avg_opponent_rating: 1440, missing_opponent_rating_games: 0 },
      drivers: [],
      highlights: { best_surprises: [], worst_surprises: [] },
      window: { start: '2025-01-01T00:00:00Z', end: '2025-01-15T00:00:00Z' },
      trajectory: [
        { played_at: '2025-01-02T10:00:00Z', rating: 1420 },
        { played_at: '2025-01-03T10:00:00Z', rating: 1435 },
        { played_at: '2025-01-04T10:00:00Z', rating: 1440 },
      ],
      chart_series: [
        { at: '2025-01-02T10:00:00Z', rating: 1420, source: 'game' },
        { at: '2025-01-03T10:00:00Z', rating: 1435, source: 'game' },
        { at: '2025-01-04T10:00:00Z', rating: 1440, source: 'game' },
        { at: '2025-01-05T08:00:00Z', rating: 1455, source: 'snapshot' },
      ],
      confidence: 'low',
      insufficient_data: false,
    });
    mockGetRatingHistory.mockResolvedValue([]);

    render(<RatingInsights />);

    await waitFor(() => {
      // The accessible chart summary reflects the fused endpoints, proving the
      // chart drew chart_series (4 points to 1455), not the trajectory (1440).
      expect(screen.getByRole('img')).toHaveAttribute(
        'aria-label',
        'Rating over time, 4 points from 1420 to 1455',
      );
      // Card matches, and only the start is flagged estimated.
      expect(screen.getByText('+35')).toBeInTheDocument();
      expect(screen.getByText(/1420 → 1455 \(start est\. from games\)/)).toBeInTheDocument();
      // The series mixes game points with a snapshot anchor — caption says so.
      expect(screen.getByText('From your games and rating snapshots')).toBeInTheDocument();
    });
  });

  it('falls back to snapshot history when chart_series has fewer than 2 points', async () => {
    // One game + no snapshot anchors → backend emits a 1-point series. A
    // 1-point line is useless; the recorded snapshot history must chart instead.
    mockGetRatingExplain.mockResolvedValue({
      rating: {
        start: 1440, end: 1440, net_change: 0,
        is_estimated: true, start_is_estimated: true, end_is_estimated: true,
        reference_rating: 1440, reference_is_approx: true,
      },
      stats: { games: 1, wins: 1, draws: 0, losses: 0, actual_minus_expected: 0.4, avg_opponent_rating: 1450, missing_opponent_rating_games: 0 },
      drivers: [],
      highlights: { best_surprises: [], worst_surprises: [] },
      window: { start: '2025-01-01T00:00:00Z', end: '2025-01-15T00:00:00Z' },
      trajectory: [{ played_at: '2025-01-02T10:00:00Z', rating: 1440 }],
      chart_series: [{ at: '2025-01-02T10:00:00Z', rating: 1440, source: 'game' }],
      confidence: 'low',
      insufficient_data: true,
    });
    mockGetRatingHistory.mockResolvedValue([
      { rating: 1200, recorded_at: '2025-01-01T00:00:00Z' },
      { rating: 1240, recorded_at: '2025-01-05T00:00:00Z' },
    ]);

    render(<RatingInsights />);

    await waitFor(() => {
      expect(screen.getByRole('img')).toHaveAttribute(
        'aria-label',
        'Rating over time, 2 points from 1200 to 1240',
      );
      expect(screen.getByText('From recorded snapshots')).toBeInTheDocument();
    });
  });

  it('annotates only the end anchor when just end_is_estimated is set', async () => {
    // Snapshot start + game-Elo end: the common shape where only the end
    // anchor is estimated.
    mockGetRatingExplain.mockResolvedValue({
      rating: {
        start: 1480, end: 1500, net_change: 20,
        is_estimated: true, start_is_estimated: false, end_is_estimated: true,
        reference_rating: 1480, reference_is_approx: false,
      },
      stats: { games: 5, wins: 3, draws: 1, losses: 1, actual_minus_expected: 0.6, avg_opponent_rating: 1490, missing_opponent_rating_games: 0 },
      drivers: [],
      highlights: { best_surprises: [], worst_surprises: [] },
      window: { start: '2025-01-01T00:00:00Z', end: '2025-01-15T00:00:00Z' },
      trajectory: [
        { played_at: '2025-01-02T10:00:00Z', rating: 1490 },
        { played_at: '2025-01-03T10:00:00Z', rating: 1500 },
      ],
      chart_series: [
        { at: '2025-01-01T08:00:00Z', rating: 1480, source: 'snapshot' },
        { at: '2025-01-02T10:00:00Z', rating: 1490, source: 'game' },
        { at: '2025-01-03T10:00:00Z', rating: 1500, source: 'game' },
      ],
      confidence: 'low',
      insufficient_data: false,
    });
    mockGetRatingHistory.mockResolvedValue([]);

    render(<RatingInsights />);

    await waitFor(() => {
      expect(screen.getByText(/1480 → 1500 \(end est\. from games\)/)).toBeInTheDocument();
      expect(screen.queryByText(/start est\./)).not.toBeInTheDocument();
    });
  });

  it('shows opponent name and rating in highlight rows', async () => {
    mockGetRatingExplain.mockResolvedValue({
      rating: { start: 1200, end: 1250, net_change: 50, reference_rating: 1220, reference_is_approx: false },
      stats: { games: 25, wins: 15, draws: 3, losses: 7, actual_minus_expected: 2.5, avg_opponent_rating: 1230, missing_opponent_rating_games: 0 },
      drivers: [],
      highlights: {
        best_surprises: [{
          opponent_rating: 1450, opponent_username: 'MagnusFan99', result: 'Win',
          expected_score: 0.21, rating_diff: 230, game_id: 'g1',
          played_at: '2025-01-10T10:00:00Z', url: 'https://chess.com/game/g1',
        }],
        worst_surprises: [],
      },
      window: { start: '2025-01-01T00:00:00Z', end: '2025-01-15T00:00:00Z' },
    });
    mockGetRatingHistory.mockResolvedValue([]);

    render(<RatingInsights />);

    await waitFor(() => {
      expect(screen.getByText(/vs MagnusFan99/)).toBeInTheDocument();
      expect(screen.getByText(/\(1450\)/)).toBeInTheDocument();
      expect(screen.getByText(/230 pts higher/)).toBeInTheDocument();
    });
  });
});
