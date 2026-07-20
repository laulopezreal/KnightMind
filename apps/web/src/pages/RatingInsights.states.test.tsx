import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import RatingInsights from './RatingInsights';
import { setupMockLocalStorage } from '../test/helpers';

// Dim 25: offline affordance + stale-response guard on username change.

const mockNavigate = vi.fn();
let mockUsername = 'alice';
const mockSetEditorOpen = vi.fn();

vi.mock('react-router-dom', () => ({ useNavigate: () => mockNavigate }));
vi.mock('../context/ChessUsernameContext', () => ({
    useChessUsername: () => ({ username: mockUsername, setEditorOpen: mockSetEditorOpen }),
}));

const mockGetRatingExplain = vi.fn();
const mockGetRatingHistory = vi.fn();
const mockCreateSnapshot = vi.fn();
const mockGetRecentSessions = vi.fn();

vi.mock('../api/ratings', () => ({
    getRatingExplain: (...a: unknown[]) => mockGetRatingExplain(...a),
    getRatingHistory: (...a: unknown[]) => mockGetRatingHistory(...a),
    createSnapshot: (...a: unknown[]) => mockCreateSnapshot(...a),
}));
vi.mock('../api/sessions', () => ({
    getRecentSessions: (...a: unknown[]) => mockGetRecentSessions(...a),
}));

vi.mock('recharts', () => ({
    LineChart: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
    Line: () => <div />, XAxis: () => <div />, YAxis: () => <div />,
    Tooltip: () => <div />, ResponsiveContainer: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
    ReferenceDot: () => <div />,
}));

const EXPLAIN_WITH_GAMES = {
    rating: { start: 1200, end: 1250, net_change: 50, reference_rating: 1220, reference_is_approx: false },
    stats: { games: 25, wins: 15, draws: 3, losses: 7, actual_minus_expected: 2.5, avg_opponent_rating: 1230, missing_opponent_rating_games: 0 },
    drivers: [], highlights: { best_surprises: [], worst_surprises: [] },
    window: { start: '2025-01-01T00:00:00Z', end: '2025-01-15T00:00:00Z' },
};

function setOnline(value: boolean) {
    Object.defineProperty(navigator, 'onLine', { value, configurable: true });
}

describe('RatingInsights data states', () => {
    beforeEach(() => {
        vi.clearAllMocks();
        setupMockLocalStorage();
        mockUsername = 'alice';
        mockGetRecentSessions.mockResolvedValue([{ session_id: 's1' }]);
    });
    afterEach(() => {
        setOnline(true);
        vi.unstubAllGlobals();
    });

    it('shows an offline affordance (not a generic error) when a fetch fails while offline', async () => {
        setOnline(false);
        mockGetRatingExplain.mockRejectedValue(new Error('Network request failed'));
        mockGetRatingHistory.mockRejectedValue(new Error('Network request failed'));

        render(<RatingInsights />);

        await waitFor(() => {
            expect(screen.getByRole('alert')).toHaveTextContent(/offline/i);
        });
        expect(screen.queryByText('Network request failed')).not.toBeInTheDocument();
    });

    it('ignores a stale error from a superseded username', async () => {
        setOnline(true);
        let rejectAlice!: (reason?: unknown) => void;
        mockGetRatingExplain
            .mockImplementationOnce(() => new Promise((_, rej) => { rejectAlice = rej; }))
            .mockResolvedValue(EXPLAIN_WITH_GAMES);
        mockGetRatingHistory.mockResolvedValue([]);

        const { rerender } = render(<RatingInsights />);

        mockUsername = 'bob';
        rerender(<RatingInsights />);
        await waitFor(() => expect(screen.getByText('15W - 3D - 7L')).toBeInTheDocument());

        rejectAlice(new Error('alice stale error'));
        await new Promise((r) => setTimeout(r, 20));

        expect(screen.queryByRole('alert')).not.toBeInTheDocument();
        expect(screen.getByText('15W - 3D - 7L')).toBeInTheDocument();
    });
});
