import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import Dashboard from './Dashboard';

// Dim 25: offline affordance + stale-response guard on username change.

const mockNavigate = vi.fn();
let mockUsername = 'alice';

vi.mock('react-router-dom', () => ({ useNavigate: () => mockNavigate }));
vi.mock('../context/ChessUsernameContext', () => ({
    useChessUsername: () => ({ username: mockUsername }),
}));

const mockGetDashboardSummary = vi.fn();
const mockGetTrickyPuzzles = vi.fn();
const mockGetRecentSessions = vi.fn();

vi.mock('../api/users', () => ({
    getDashboardSummary: (...a: unknown[]) => mockGetDashboardSummary(...a),
    getTrickyPuzzles: (...a: unknown[]) => mockGetTrickyPuzzles(...a),
}));
vi.mock('../api/sessions', () => ({
    getRecentSessions: (...a: unknown[]) => mockGetRecentSessions(...a),
}));

vi.mock('../components/HeroTrainCard', () => ({ HeroTrainCard: () => <div data-testid="hero-card" /> }));
vi.mock('../components/RecentlyTrickyCard', () => ({ RecentlyTrickyCard: () => <div /> }));
vi.mock('../components/MomentumCard', () => ({ MomentumCard: () => <div /> }));
vi.mock('../components/StreakCard', () => ({ StreakCard: () => <div /> }));
vi.mock('../components/RecentSessionsCard', () => ({ RecentSessionsCard: () => <div /> }));

const SUMMARY = {
    schedule: { due_now: 0, next_review_at: null },
    needs_warmup: false,
    days_since_last_session: 0,
    total_sessions: 3,
    recent_form: [],
    training_streak_days: 0,
    last_session_at: null,
};

function setOnline(value: boolean) {
    Object.defineProperty(navigator, 'onLine', { value, configurable: true });
}

describe('Dashboard data states', () => {
    beforeEach(() => {
        vi.clearAllMocks();
        mockUsername = 'alice';
        mockGetRecentSessions.mockResolvedValue([]);
        mockGetTrickyPuzzles.mockResolvedValue({ puzzles: [], total_count: 0 });
    });
    afterEach(() => setOnline(true));

    it('announces a loading status via the shared skeleton while the fetch is in flight', () => {
        // Dashboard summary never resolves -> the page stays in its loading state.
        // The shared DataStateSkeleton must announce it as a role="status" region
        // named by the sr-only label (accessible loading state, not a bare spinner).
        mockGetDashboardSummary.mockReturnValue(new Promise(() => {}));

        render(<Dashboard />);

        // status is a live region, so the sr-only label is text content, not the
        // accessible name — assert both the region and its announcement.
        const status = screen.getByRole('status');
        expect(status).toHaveTextContent(/loading dashboard/i);
    });

    it('shows an offline affordance (not a generic error) when a fetch fails while offline', async () => {
        setOnline(false);
        mockGetDashboardSummary.mockRejectedValue(new Error('Network request failed'));

        render(<Dashboard />);

        await waitFor(() => {
            expect(screen.getByRole('alert')).toHaveTextContent(/offline/i);
        });
        expect(screen.queryByText('Network request failed')).not.toBeInTheDocument();
    });

    it('ignores a stale error from a superseded username', async () => {
        setOnline(true);
        let rejectAlice!: (reason?: unknown) => void;
        mockGetDashboardSummary
            .mockImplementationOnce(() => new Promise((_, rej) => { rejectAlice = rej; }))
            .mockResolvedValue(SUMMARY);

        const { rerender } = render(<Dashboard />);

        // Switch to a new username mid-flight; the newer request resolves.
        mockUsername = 'bob';
        rerender(<Dashboard />);
        await waitFor(() => expect(screen.getByTestId('hero-card')).toBeInTheDocument());

        // The superseded 'alice' request now rejects — it must not surface an error.
        rejectAlice(new Error('alice stale error'));
        await new Promise((r) => setTimeout(r, 20));

        expect(screen.queryByRole('alert')).not.toBeInTheDocument();
        expect(screen.getByTestId('hero-card')).toBeInTheDocument();
    });
});
