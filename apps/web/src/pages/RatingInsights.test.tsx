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

  it('should show prompt when no username', () => {
    mockUsername = '';
    render(<RatingInsights />);

    expect(screen.getByText(/set a username/i)).toBeInTheDocument();
    expect(screen.getByText('Set Username')).toBeInTheDocument();
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
  });

  it('should show error when API fails', async () => {
    mockGetRatingExplain.mockRejectedValue(new Error('Server error'));
    mockGetRatingHistory.mockRejectedValue(new Error('Server error'));

    render(<RatingInsights />);

    await waitFor(() => {
      expect(screen.getByText('Server error')).toBeInTheDocument();
    });
  });

  it('should show onboarding when no games', async () => {
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
      expect(screen.getByText(/Step 1/)).toBeInTheDocument();
      expect(screen.getByText(/Step 2/)).toBeInTheDocument();
    });
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

  it('should show Record Snapshot button', () => {
    mockGetRatingExplain.mockReturnValue(new Promise(() => {}));
    mockGetRatingHistory.mockReturnValue(new Promise(() => {}));

    render(<RatingInsights />);

    expect(screen.getByText('Record Snapshot')).toBeInTheDocument();
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
});
