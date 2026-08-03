import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import Insights from './Insights';

// Dim 25: offline affordance + stale-response guard on username change.

const mockNavigate = vi.fn();
let mockUsername = 'alice';

vi.mock('react-router-dom', () => ({
  useNavigate: () => mockNavigate,
  Link: ({ children, to, ...rest }: { children: React.ReactNode; to: string; [key: string]: unknown }) => <a href={to} {...rest}>{children}</a>,
}));
vi.mock('../context/ChessUsernameContext', () => ({
    useChessUsername: () => ({ username: mockUsername }),
}));

const mockGetMotifPerformance = vi.fn();
const mockGetMotifTrends = vi.fn();
const mockGetTrickyPuzzles = vi.fn();
const mockGetMistakeCauses = vi.fn();
const mockGetTodaysFocus = vi.fn();
const mockGetMistakePatterns = vi.fn();

vi.mock('../api/users', () => ({
    getMotifPerformance: (...a: unknown[]) => mockGetMotifPerformance(...a),
    getMotifTrends: (...a: unknown[]) => mockGetMotifTrends(...a),
    getTrickyPuzzles: (...a: unknown[]) => mockGetTrickyPuzzles(...a),
    getMistakeCauses: (...a: unknown[]) => mockGetMistakeCauses(...a),
    getTodaysFocus: (...a: unknown[]) => mockGetTodaysFocus(...a),
    getMistakePatterns: (...a: unknown[]) => mockGetMistakePatterns(...a),
}));

vi.mock('../components/TacticalRadar', () => ({
    TacticalRadar: () => <div data-testid="tactical-radar" />,
}));
vi.mock('../components/MotifTrends', () => ({ MotifTrends: () => <div /> }));
vi.mock('../components/RecentlyTrickyCard', () => ({ RecentlyTrickyCard: () => <div /> }));

function setOnline(value: boolean) {
    Object.defineProperty(navigator, 'onLine', { value, configurable: true });
}

describe('Insights data states', () => {
    beforeEach(() => {
        vi.clearAllMocks();
        mockUsername = 'alice';
        mockGetTrickyPuzzles.mockResolvedValue({ puzzles: [], total_count: 0 });
        mockGetMistakeCauses.mockResolvedValue({
      username: 'testuser',
      causes: [],
      total_diagnosed: 0,
      pending: 0,
      min_for_ranking: 4,
    });
        mockGetTodaysFocus.mockResolvedValue({
            username: 'testuser',
            focus: null,
            below_threshold: 0,
            pending: 0,
        });
    mockGetMistakePatterns.mockResolvedValue({ username: 'testuser', patterns: [], below_threshold: 0, pending: 0 });
    });
    afterEach(() => setOnline(true));

    it('shows an offline affordance (not a generic error) when a fetch fails while offline', async () => {
        setOnline(false);
        mockGetMotifPerformance.mockRejectedValue(new Error('Network request failed'));
        mockGetMotifTrends.mockRejectedValue(new Error('Network request failed'));

        render(<Insights />);

        await waitFor(() => {
            expect(screen.getByRole('alert')).toHaveTextContent(/offline/i);
        });
        expect(screen.queryByText('Network request failed')).not.toBeInTheDocument();
    });

    // The header now comes from a single InsightsShell wrapping every branch.
    // These pin that: re-inlining a header into one branch (and dropping it from
    // another) is exactly how this page grew a duplicate in the first place.
    it('keeps the page h1 while loading', () => {
        mockGetMotifPerformance.mockReturnValue(new Promise(() => {}));
        mockGetMotifTrends.mockReturnValue(new Promise(() => {}));

        render(<Insights />);

        expect(screen.getByRole('heading', { level: 1, name: 'Insights' })).toBeInTheDocument();
    });

    it('keeps the page h1 in the error state', async () => {
        setOnline(true);
        mockGetMotifPerformance.mockRejectedValue(new Error('Boom 500'));
        mockGetMotifTrends.mockRejectedValue(new Error('Boom 500'));

        render(<Insights />);

        await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('Boom 500'));
        expect(screen.getByRole('heading', { level: 1, name: 'Insights' })).toBeInTheDocument();
    });

    it('ignores a stale error from a superseded username', async () => {
        setOnline(true);
        let rejectAlice!: (reason?: unknown) => void;
        mockGetMotifPerformance
            .mockImplementationOnce(() => new Promise((_, rej) => { rejectAlice = rej; }))
            .mockResolvedValue({ motifs: [{ name: 'Fork', accuracy: 0.8, total_puzzles: 10, correct: 8 }] });
        mockGetMotifTrends.mockResolvedValue({ motif_trends: [], window_days: 30 });

        const { rerender } = render(<Insights />);

        mockUsername = 'bob';
        rerender(<Insights />);
        await waitFor(() => expect(screen.getByTestId('tactical-radar')).toBeInTheDocument());

        rejectAlice(new Error('alice stale error'));
        await new Promise((r) => setTimeout(r, 20));

        expect(screen.queryByRole('alert')).not.toBeInTheDocument();
        expect(screen.getByTestId('tactical-radar')).toBeInTheDocument();
    });
});
