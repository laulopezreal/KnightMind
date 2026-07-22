import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
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
        // Route by username, not call order: the page now fetches exactly once
        // per (username, window, control), so call-order mocks would race.
        mockGetRatingExplain.mockImplementation((username: string) =>
            username === 'alice'
                ? new Promise((_, rej) => { rejectAlice = rej; })
                : Promise.resolve(EXPLAIN_WITH_GAMES)
        );
        mockGetRatingHistory.mockResolvedValue([]);

        const { rerender } = render(<RatingInsights />);
        // Wait until alice's (never-resolving) explain request is in flight.
        await waitFor(() =>
            expect(mockGetRatingExplain.mock.calls.some(c => c[0] === 'alice')).toBe(true)
        );

        mockUsername = 'bob';
        rerender(<RatingInsights />);
        await waitFor(() => expect(screen.getByText('15W - 3D - 7L')).toBeInTheDocument());

        rejectAlice(new Error('alice stale error'));
        await new Promise((r) => setTimeout(r, 20));

        expect(screen.queryByRole('alert')).not.toBeInTheDocument();
        expect(screen.getByText('15W - 3D - 7L')).toBeInTheDocument();
    });
});

const EXPLAIN_NO_GAMES = {
    rating: { start: null, end: null, net_change: null, reference_rating: 0, reference_is_approx: false },
    stats: { games: 0, wins: 0, draws: 0, losses: 0, actual_minus_expected: null, avg_opponent_rating: null, missing_opponent_rating_games: 0 },
    drivers: [], highlights: { best_surprises: [], worst_surprises: [] },
    window: null,
};

const SNAPSHOTS = [
    { rating: 1200, recorded_at: '2025-01-01T00:00:00Z' },
    { rating: 1240, recorded_at: '2025-01-05T00:00:00Z' },
];

// The explain and history fetches are decoupled: history is keyed on
// [username, timeControl] only, explain runs concurrently with the sessions
// probe in fallback_7d mode, and each has its own stale guard + error slot.
describe('RatingInsights fetch concurrency & error isolation', () => {
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

    it('surfaces a history failure even when explain later succeeds', async () => {
        mockGetRatingHistory.mockRejectedValue(new Error('history down'));
        // Delay the probe so the history rejection lands BEFORE explain begins:
        // explain's own error-reset must not swallow the history error.
        mockGetRecentSessions.mockImplementation(
            () => new Promise(res => setTimeout(() => res([{ session_id: 's1' }]), 10))
        );
        mockGetRatingExplain.mockResolvedValue(EXPLAIN_WITH_GAMES);

        render(<RatingInsights />);

        await waitFor(() => expect(screen.getByText('15W - 3D - 7L')).toBeInTheDocument());
        expect(screen.getByRole('alert')).toHaveTextContent('history down');
    });

    it('does not refetch history on a window toggle (explain only)', async () => {
        mockGetRatingExplain.mockResolvedValue(EXPLAIN_WITH_GAMES);
        mockGetRatingHistory.mockResolvedValue(SNAPSHOTS);

        render(<RatingInsights />);
        await waitFor(() => expect(screen.getByText('15W - 3D - 7L')).toBeInTheDocument());
        expect(mockGetRatingHistory).toHaveBeenCalledTimes(1);
        const explainCalls = mockGetRatingExplain.mock.calls.length;

        fireEvent.click(screen.getByText('Last 7 Days'));

        await waitFor(() => expect(mockGetRatingExplain.mock.calls.length).toBeGreaterThan(explainCalls));
        await new Promise(r => setTimeout(r, 30));
        expect(mockGetRatingHistory).toHaveBeenCalledTimes(1);
    });

    it('refetches history when the time control changes', async () => {
        mockGetRatingExplain.mockResolvedValue(EXPLAIN_WITH_GAMES);
        mockGetRatingHistory.mockResolvedValue(SNAPSHOTS);

        render(<RatingInsights />);
        await waitFor(() => expect(mockGetRatingHistory).toHaveBeenCalledWith('alice', 'rapid'));

        fireEvent.click(screen.getByText('Blitz'));

        await waitFor(() => expect(mockGetRatingHistory).toHaveBeenCalledWith('alice', 'blitz'));
    });

    it('fires explain alongside a still-pending sessions probe in fallback_7d mode', async () => {
        localStorage.setItem('knightmind:ratings:window', 'last_7_days');
        mockGetRecentSessions.mockReturnValue(new Promise(() => {}));
        mockGetRatingExplain.mockReturnValue(new Promise(() => {}));
        mockGetRatingHistory.mockReturnValue(new Promise(() => {}));

        render(<RatingInsights />);

        await waitFor(() => expect(mockGetRatingExplain).toHaveBeenCalledTimes(1));
        // Date-windowed, not session-windowed.
        expect(mockGetRatingExplain).toHaveBeenCalledWith('alice', 'rapid', undefined, expect.any(String));
    });

    it('serializes explain behind the sessions probe in session mode (history still concurrent)', async () => {
        mockGetRecentSessions.mockReturnValue(new Promise(() => {}));
        mockGetRatingExplain.mockResolvedValue(EXPLAIN_WITH_GAMES);
        mockGetRatingHistory.mockResolvedValue(SNAPSHOTS);

        render(<RatingInsights />);

        await waitFor(() => expect(mockGetRatingHistory).toHaveBeenCalledTimes(1));
        await new Promise(r => setTimeout(r, 30));
        expect(mockGetRatingExplain).not.toHaveBeenCalled();
    });

    it('Retry recovers a history-only failure', async () => {
        mockGetRatingExplain.mockResolvedValue(EXPLAIN_NO_GAMES);
        mockGetRatingHistory
            .mockRejectedValueOnce(new Error('history down'))
            .mockResolvedValue(SNAPSHOTS);

        render(<RatingInsights />);
        await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('history down'));

        fireEvent.click(screen.getByRole('button', { name: 'Retry loading rating insights' }));

        await waitFor(() => expect(screen.getByText(/Not enough games in this window/i)).toBeInTheDocument());
        expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    });

    it('Retry while the sessions probe is pending never fires an unwindowed explain', async () => {
        mockGetRecentSessions.mockReturnValue(new Promise(() => {}));
        mockGetRatingHistory.mockRejectedValue(new Error('history down'));
        mockGetRatingExplain.mockResolvedValue(EXPLAIN_WITH_GAMES);

        render(<RatingInsights />);
        await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('history down'));

        fireEvent.click(screen.getByRole('button', { name: 'Retry loading rating insights' }));
        await new Promise(r => setTimeout(r, 30));

        // 'session' window has no resolved session id yet — an explain call now
        // would silently be unwindowed. It must not happen.
        expect(mockGetRatingExplain).not.toHaveBeenCalled();
        // History itself was retried.
        expect(mockGetRatingHistory.mock.calls.length).toBeGreaterThanOrEqual(2);
    });

    it('a window toggle does not drop the in-flight history response (independent guards)', async () => {
        let resolveHistory!: (v: unknown) => void;
        mockGetRatingHistory.mockReturnValue(new Promise(res => { resolveHistory = res; }));
        mockGetRatingExplain.mockResolvedValue(EXPLAIN_WITH_GAMES);

        render(<RatingInsights />);
        await waitFor(() => expect(screen.getByText('15W - 3D - 7L')).toBeInTheDocument());

        // Toggling begins a new explain request; it must NOT invalidate the
        // still-pending history request.
        fireEvent.click(screen.getByText('Last 7 Days'));
        await waitFor(() => expect(mockGetRatingExplain.mock.calls.length).toBeGreaterThanOrEqual(2));

        resolveHistory(SNAPSHOTS);
        // EXPLAIN_WITH_GAMES has no trajectory, so the chart falls back to the
        // snapshot history — its presence proves the response was applied.
        await waitFor(() => expect(screen.getByText(/From recorded snapshots/i)).toBeInTheDocument());
    });

    it('never flashes the first-import onboarding while history is unknown', async () => {
        let resolveHistory!: (v: unknown) => void;
        mockGetRatingHistory.mockReturnValue(new Promise(res => { resolveHistory = res; }));
        mockGetRatingExplain.mockResolvedValue(EXPLAIN_NO_GAMES);

        render(<RatingInsights />);

        // Explain landed (0 games) but history is unknown: hold a placeholder,
        // never the "brand new user" onboarding.
        await waitFor(() => expect(screen.getByText('Loading rating history...')).toBeInTheDocument());
        expect(screen.queryByText(/No Rapid games yet/i)).not.toBeInTheDocument();

        resolveHistory(SNAPSHOTS);
        await waitFor(() => expect(screen.getByText(/Not enough games in this window/i)).toBeInTheDocument());
        expect(screen.queryByText(/No Rapid games yet/i)).not.toBeInTheDocument();
    });

    it('resets history on a time-control switch so stale snapshots never mislabel the chart', async () => {
        mockGetRatingExplain.mockResolvedValue(EXPLAIN_NO_GAMES);
        let resolveBlitzHistory!: (v: unknown) => void;
        mockGetRatingHistory.mockImplementation((_u: string, tc: string) =>
            tc === 'rapid'
                ? Promise.resolve(SNAPSHOTS)
                : new Promise(res => { resolveBlitzHistory = res; })
        );

        render(<RatingInsights />);
        await waitFor(() => expect(screen.getByText(/Not enough games in this window/i)).toBeInTheDocument());
        expect(screen.getByText(/From recorded snapshots/i)).toBeInTheDocument();

        fireEvent.click(screen.getByText('Blitz'));

        // Rapid's snapshots must not linger as if they were Blitz data.
        await waitFor(() => expect(screen.queryByText(/From recorded snapshots/i)).not.toBeInTheDocument());

        resolveBlitzHistory([]);
        await waitFor(() => expect(screen.getByText(/No Blitz games yet/i)).toBeInTheDocument());
    });
});
