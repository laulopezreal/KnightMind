import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import Dashboard from './Dashboard';

// A tile that throws while *rendering* must cost only that tile — the same
// outcome the page already gives a tile whose *data* failed (Promise.allSettled
// in the strip loader). Before per-card boundaries, one bad tile unmounted the
// whole app, nav included.

const mockNavigate = vi.fn();

vi.mock('react-router-dom', () => ({
    useNavigate: () => mockNavigate,
    Link: ({ children }: { children: React.ReactNode }) => <a>{children}</a>,
}));
vi.mock('../context/ChessUsernameContext', () => ({
    useChessUsername: () => ({ username: 'alice' }),
}));

const mockGetDashboardSummary = vi.fn();
const mockGetTrickyPuzzles = vi.fn();
const mockGetRecentSessions = vi.fn();
const mockGetMotifPerformance = vi.fn();
const mockGetUserStatus = vi.fn();
const mockGetTodaysFocus = vi.fn();
const mockGetRatingExplain = vi.fn();

vi.mock('../api/users', () => ({
    getDashboardSummary: (...a: unknown[]) => mockGetDashboardSummary(...a),
    getTrickyPuzzles: (...a: unknown[]) => mockGetTrickyPuzzles(...a),
    getMotifPerformance: (...a: unknown[]) => mockGetMotifPerformance(...a),
    getUserStatus: (...a: unknown[]) => mockGetUserStatus(...a),
    getTodaysFocus: (...a: unknown[]) => mockGetTodaysFocus(...a),
}));
vi.mock('../api/ratings', () => ({
    getRatingExplain: (...a: unknown[]) => mockGetRatingExplain(...a),
}));
vi.mock('../api/sessions', () => ({
    getRecentSessions: (...a: unknown[]) => mockGetRecentSessions(...a),
}));

// The failing tile. Mirrors the real #321 crash: a lookup miss on a value that
// arrived from the server unvalidated.
vi.mock('../components/MomentumCard', () => ({
    MomentumCard: () => {
        throw new Error("Cannot read properties of undefined (reading 'color')");
    },
}));

vi.mock('../components/HeroTrainCard', () => ({ HeroTrainCard: () => <div data-testid="hero-card" /> }));
vi.mock('../components/StreakCard', () => ({ StreakCard: () => <div data-testid="streak-card" /> }));
vi.mock('../components/RecentlyTrickyCard', () => ({ RecentlyTrickyCard: () => <div /> }));
vi.mock('../components/RecentSessionsCard', () => ({ RecentSessionsCard: () => <div data-testid="sessions-card" /> }));
vi.mock('../components/WeakestMotifCard', () => ({ WeakestMotifCard: () => <div /> }));
vi.mock('../components/RatingDeltaCard', () => ({ RatingDeltaCard: () => <div /> }));

const SUMMARY = {
    schedule: { due_now: 2, due_in_4h: 0, next_review_at: null },
    needs_warmup: false,
    days_since_last_session: 0,
    total_sessions: 3,
    // A real RecentFormData, not `[]`. The page now hides the momentum tile for
    // a user who has never trained, so an empty form would mean the tile — and
    // the error boundary this test is about — never render at all.
    recent_form: {
        last_20_results: ['pass' as const, 'fail' as const],
        accuracy: 0.5,
        trend: 'steady' as const,
        sample_size: 2,
        insufficient_data: false,
    },
    training_streak_days: 4,
    last_session_at: null,
    daily_practice: { completed_today: false, completed_session_at: null },
};

describe('Dashboard tile containment', () => {
    beforeEach(() => {
        vi.clearAllMocks();
        vi.spyOn(console, 'error').mockImplementation(() => {});
        mockGetDashboardSummary.mockResolvedValue(SUMMARY);
        mockGetRecentSessions.mockResolvedValue([
            { session_id: 1, started_at: '2026-01-01T00:00:00Z', puzzles_solved: 3, accuracy: 0.9 },
        ]);
        mockGetTrickyPuzzles.mockResolvedValue({ puzzles: [], total_count: 0 });
        mockGetMotifPerformance.mockResolvedValue({ motifs: [], weakest_motifs: [], total_motifs_practiced: 0 });
        mockGetUserStatus.mockResolvedValue({ has_new_games: false });
        mockGetTodaysFocus.mockResolvedValue({ username: 'alice', focus: null, below_threshold: 0, pending: 0 });
        mockGetRatingExplain.mockResolvedValue({
            rating: { net_change: null, start: null, end: null },
            stats: { games: 0 },
            confidence: 'low',
        });
    });

    it('keeps the page and its other tiles when one tile throws on render', async () => {
        render(<Dashboard />);

        await waitFor(() => expect(screen.getByTestId('hero-card')).toBeInTheDocument());

        // The broken tile degrades to a labelled, retryable card…
        const fallback = screen.getByRole('status');
        expect(fallback).toHaveTextContent('Momentum');
        expect(fallback).toHaveTextContent(/couldn’t be displayed/);
        expect(screen.getByRole('button', { name: /try loading momentum again/i })).toBeInTheDocument();

        // …and everything around it still renders.
        expect(screen.getByRole('heading', { name: 'Dashboard' })).toBeInTheDocument();
        expect(screen.getByTestId('streak-card')).toBeInTheDocument();
        expect(screen.getByTestId('sessions-card')).toBeInTheDocument();
    });
});
