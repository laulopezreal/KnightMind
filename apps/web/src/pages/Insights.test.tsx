import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import Insights from './Insights';

const mockNavigate = vi.fn();
let mockUsername = 'testplayer';

vi.mock('react-router-dom', () => ({
  useNavigate: () => mockNavigate,
}));

vi.mock('../context/ChessUsernameContext', () => ({
  useChessUsername: () => ({ username: mockUsername }),
}));

const mockGetMotifPerformance = vi.fn();
const mockGetMotifTrends = vi.fn();
const mockGetTrickyPuzzles = vi.fn();

vi.mock('../api/users', () => ({
  getMotifPerformance: (...args: unknown[]) => mockGetMotifPerformance(...args),
  getMotifTrends: (...args: unknown[]) => mockGetMotifTrends(...args),
  getTrickyPuzzles: (...args: unknown[]) => mockGetTrickyPuzzles(...args),
}));

vi.mock('../components/TacticalRadar', () => ({
  TacticalRadar: ({ motifs }: { motifs: unknown[] }) => (
    <div data-testid="tactical-radar">Radar ({motifs.length} motifs)</div>
  ),
}));

vi.mock('../components/MotifTrends', () => ({
  MotifTrends: () => <div data-testid="motif-trends">MotifTrends</div>,
}));

vi.mock('../components/RecentlyTrickyCard', () => ({
  RecentlyTrickyCard: ({ puzzles }: { puzzles: unknown[] }) => (
    <div data-testid="recently-tricky-card">Tricky ({puzzles.length} puzzles)</div>
  ),
}));

describe('Insights', () => {
  beforeEach(() => {
    vi.resetAllMocks();
    mockUsername = 'testplayer';
    mockGetMotifPerformance.mockRejectedValue(new Error('Not loaded'));
    mockGetMotifTrends.mockRejectedValue(new Error('Not loaded'));
    mockGetTrickyPuzzles.mockResolvedValue({ puzzles: [], total_count: 0 });
  });

  it('should redirect to home when no username', () => {
    mockUsername = '';
    render(<Insights />);

    expect(mockNavigate).toHaveBeenCalledWith('/');
  });

  it('should show loading spinner initially', () => {
    // Keep promises pending
    mockGetMotifPerformance.mockReturnValue(new Promise(() => {}));
    mockGetMotifTrends.mockReturnValue(new Promise(() => {}));

    render(<Insights />);

    expect(screen.getByText('Loading insights...')).toBeInTheDocument();
    // The page heading renders even while loading (no bare early-return).
    expect(screen.getByRole('heading', { name: /insights/i })).toBeInTheDocument();
  });

  it('should show error state when API fails', async () => {
    mockGetMotifPerformance.mockRejectedValue(new Error('API down'));
    mockGetMotifTrends.mockRejectedValue(new Error('API down'));

    render(<Insights />);

    await waitFor(() => {
      expect(screen.getByText('API down')).toBeInTheDocument();
    });

    expect(screen.getByText('Retry')).toBeInTheDocument();
    // The page heading renders in the error state too (previously it did not).
    expect(screen.getByRole('heading', { name: /insights/i })).toBeInTheDocument();
  });

  it('should show empty state when no motif data', async () => {
    mockGetMotifPerformance.mockResolvedValue({ motifs: [] });
    mockGetMotifTrends.mockResolvedValue({ motif_trends: [], window_days: 30 });

    render(<Insights />);

    await waitFor(() => {
      expect(screen.getByText('No puzzle data yet')).toBeInTheDocument();
    });

    expect(screen.getByText('Start Puzzles')).toBeInTheDocument();
  });

  it('should render TacticalRadar when motif data exists', async () => {
    mockGetMotifPerformance.mockResolvedValue({
      motifs: [
        { name: 'Fork', accuracy: 0.8, total_puzzles: 10, correct: 8 },
      ],
    });
    mockGetMotifTrends.mockResolvedValue({ motif_trends: [], window_days: 30 });

    render(<Insights />);

    await waitFor(() => {
      expect(screen.getByTestId('tactical-radar')).toBeInTheDocument();
    });
  });

  it('should render MotifTrends when trend data exists', async () => {
    mockGetMotifPerformance.mockResolvedValue({ motifs: [] });
    mockGetMotifTrends.mockResolvedValue({
      motif_trends: [{ name: 'Fork', data_points: [{ date: '2025-01-01', accuracy: 0.8 }] }],
      window_days: 30,
    });

    render(<Insights />);

    await waitFor(() => {
      expect(screen.getByTestId('motif-trends')).toBeInTheDocument();
    });
  });

  it('should render page heading', async () => {
    mockGetMotifPerformance.mockResolvedValue({ motifs: [] });
    mockGetMotifTrends.mockResolvedValue({ motif_trends: [], window_days: 30 });

    render(<Insights />);

    await waitFor(() => {
      expect(screen.getByText('Insights')).toBeInTheDocument();
    });
  });

  it('should render RecentlyTrickyCard when tricky puzzles exist', async () => {
    mockGetMotifPerformance.mockResolvedValue({ motifs: [] });
    mockGetMotifTrends.mockResolvedValue({ motif_trends: [], window_days: 30 });
    mockGetTrickyPuzzles.mockResolvedValue({
      puzzles: [
        { puzzle_id: '1', title: 'Fork', fail_count: 3, last_attempted_at: '2025-01-01T00:00:00Z' },
      ],
      total_count: 1,
    });

    render(<Insights />);

    await waitFor(() => {
      expect(screen.getByTestId('recently-tricky-card')).toBeInTheDocument();
    });
  });

  it('should render page even when tricky puzzles API fails', async () => {
    mockGetMotifPerformance.mockResolvedValue({
      motifs: [{ name: 'Fork', accuracy: 0.8, total_puzzles: 10, correct: 8 }],
    });
    mockGetMotifTrends.mockResolvedValue({ motif_trends: [], window_days: 30 });
    mockGetTrickyPuzzles.mockRejectedValue(new Error('API down'));

    render(<Insights />);

    await waitFor(() => {
      expect(screen.getByTestId('tactical-radar')).toBeInTheDocument();
    });

    expect(screen.queryByTestId('recently-tricky-card')).not.toBeInTheDocument();
  });

  it('should not render Puzzle Intelligence heading', async () => {
    mockGetMotifPerformance.mockResolvedValue({ motifs: [] });
    mockGetMotifTrends.mockResolvedValue({ motif_trends: [], window_days: 30 });

    render(<Insights />);

    await waitFor(() => {
      expect(screen.getByText('Insights')).toBeInTheDocument();
    });

    expect(screen.queryByText('Puzzle Intelligence')).not.toBeInTheDocument();
  });
});
