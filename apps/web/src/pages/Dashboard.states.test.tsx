import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import Dashboard from './Dashboard';

// Dim 25: offline affordance + stale-response guard on username change.

const mockNavigate = vi.fn();
let mockUsername = 'alice';

vi.mock('react-router-dom', () => ({
    useNavigate: () => mockNavigate,
    Link: ({ children }: { children: React.ReactNode }) => <a>{children}</a>,
}));
vi.mock('../context/ChessUsernameContext', () => ({
    useChessUsername: () => ({ username: mockUsername }),
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

vi.mock('../components/HeroTrainCard', () => ({
    HeroTrainCard: ({ completedToday }: { completedToday?: boolean }) => (
        <div data-testid="hero-card" data-completed-today={String(completedToday)} />
    ),
}));
vi.mock('../components/RecentlyTrickyCard', () => ({ RecentlyTrickyCard: () => <div /> }));
vi.mock('../components/MomentumCard', () => ({ MomentumCard: () => <div data-testid="momentum-card" /> }));
vi.mock('../components/StreakCard', () => ({ StreakCard: () => <div data-testid="streak-card" /> }));
vi.mock('../components/RecentSessionsCard', () => ({ RecentSessionsCard: () => <div /> }));
vi.mock('../components/WeakestMotifCard', () => ({ WeakestMotifCard: () => <div data-testid="weakest-card" /> }));
vi.mock('../components/RatingDeltaCard', () => ({ RatingDeltaCard: () => <div data-testid="rating-card" /> }));

const SUMMARY = {
    schedule: { due_now: 0, next_review_at: null },
    needs_warmup: false,
    days_since_last_session: 0,
    total_sessions: 3,
    recent_form: {
        last_20_results: [],
        accuracy: 0,
        trend: 'steady' as const,
        sample_size: 0,
        insufficient_data: true,
    },
    training_streak_days: 0,
    last_session_at: null,
    daily_practice: { completed_today: false, completed_session_at: null },
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
        // Secondary strip fetches — resolve to empty so they never interfere with
        // the core-dashboard assertions below.
        mockGetMotifPerformance.mockResolvedValue({ motifs: [], weakest_motifs: [], total_motifs_practiced: 0 });
        mockGetUserStatus.mockResolvedValue({ has_new_games: false });
        mockGetTodaysFocus.mockResolvedValue({
            username: 'alice', focus: null, below_threshold: 0, pending: 0,
        });
        mockGetRatingExplain.mockResolvedValue({ rating: { net_change: null, start: null, end: null }, stats: { games: 0 }, confidence: 'low' });
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

    // Every state must keep the page's level-one heading. The states used to be
    // early-returned in place of the whole page, which took the <h1> with them:
    // axe flagged `page-has-heading-one`, and a screen-reader user navigating by
    // headings could not tell which page had failed. getByRole is the right probe
    // here — it ignores aria-hidden subtrees, so a skeleton placeholder block
    // can't satisfy it, only a real heading outside the aria-hidden wrapper.
    it('keeps the page h1 while loading', () => {
        mockGetDashboardSummary.mockReturnValue(new Promise(() => {}));

        render(<Dashboard />);

        expect(screen.getByRole('heading', { level: 1, name: 'Dashboard' })).toBeInTheDocument();
    });

    it('keeps the page h1 in the error state', async () => {
        setOnline(true);
        mockGetDashboardSummary.mockRejectedValue(new Error('Boom'));

        render(<Dashboard />);

        await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('Boom'));
        expect(screen.getByRole('heading', { level: 1, name: 'Dashboard' })).toBeInTheDocument();
    });

    it('keeps the page h1 in the offline state', async () => {
        setOnline(false);
        mockGetDashboardSummary.mockRejectedValue(new Error('Network request failed'));

        render(<Dashboard />);

        await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent(/offline/i));
        expect(screen.getByRole('heading', { level: 1, name: 'Dashboard' })).toBeInTheDocument();
    });

    it('omits both improvement tiles for a brand-new user (no games, no motifs)', async () => {
        mockGetDashboardSummary.mockResolvedValue(SUMMARY);
        // beforeEach defaults: motifs empty, rating games 0 — the first-run case.
        render(<Dashboard />);

        await waitFor(() => expect(screen.getByTestId('hero-card')).toBeInTheDocument());
        expect(screen.queryByTestId('rating-card')).not.toBeInTheDocument();
        expect(screen.queryByTestId('weakest-card')).not.toBeInTheDocument();
    });

    it('shows the improvement tiles once there is data to show', async () => {
        mockGetDashboardSummary.mockResolvedValue(SUMMARY);
        mockGetMotifPerformance.mockResolvedValue({
            motifs: [{ name: 'fork', total_puzzles: 10, passed: 8, accuracy: 0.8, rank: 'learning', attempts: 10, insufficient_data: false }],
            weakest_motifs: ['fork'], total_motifs_practiced: 1,
        });
        mockGetRatingExplain.mockResolvedValue({
            rating: { net_change: 10, start: 1500, end: 1510 }, stats: { games: 12 }, confidence: 'high', chart_series: [],
        });

        render(<Dashboard />);

        await waitFor(() => expect(screen.getByTestId('rating-card')).toBeInTheDocument());
        expect(screen.getByTestId('weakest-card')).toBeInTheDocument();
    });

    it('shows the "new games to import" nudge only when status reports new games', async () => {
        mockGetDashboardSummary.mockResolvedValue(SUMMARY);
        mockGetUserStatus.mockResolvedValue({ has_new_games: true });

        render(<Dashboard />);

        await waitFor(() => {
            expect(screen.getByText(/new games are ready to import/i)).toBeInTheDocument();
        });
    });

    it('omits the new-games nudge when there are none', async () => {
        mockGetDashboardSummary.mockResolvedValue(SUMMARY);
        mockGetUserStatus.mockResolvedValue({ has_new_games: false });
        mockGetTodaysFocus.mockResolvedValue({
            username: 'alice', focus: null, below_threshold: 0, pending: 0,
        });

        render(<Dashboard />);

        await waitFor(() => expect(screen.getByTestId('hero-card')).toBeInTheDocument());
        expect(screen.queryByText(/new games are ready to import/i)).not.toBeInTheDocument();
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

    // ── Today's focus (the spec's daily card) ──

    it('shows the focus on the dashboard, where the daily card belongs', async () => {
        mockGetDashboardSummary.mockReset();
        mockGetDashboardSummary.mockResolvedValue(SUMMARY);
        mockGetTodaysFocus.mockResolvedValue({
            username: 'alice',
            focus: {
                cause: 'loose_piece_awareness',
                name: 'Loose Piece Syndrome',
                description: 'You skip the loose-piece scan.',
                mistakes: 9, recent_mistakes: 4, accuracy: 0.4, priority: 12,
                rationale: '9 diagnosed mistakes.', runner_up: null, trainable_now: 3,
            },
            below_threshold: 0, pending: 0,
        });

        render(<Dashboard />);
        await waitFor(() => {
            expect(screen.getByText('Loose Piece Syndrome')).toBeInTheDocument();
        });
        // This file's router mock drops `to`, so the anchor carries no href and
        // has no link role. The destination is asserted in the card's own
        // tests; here the point is that the count reached the dashboard.
        expect(screen.getByText(/3 ready/i)).toBeInTheDocument();
    });

    it('renders no focus card when there is nothing to recommend', async () => {
        // The dashboard is dense; an empty shell saying "no focus" is noise.
        mockGetDashboardSummary.mockReset();
        mockGetDashboardSummary.mockResolvedValue(SUMMARY);
        render(<Dashboard />);
        await waitFor(() => expect(mockGetTodaysFocus).toHaveBeenCalled());
        expect(screen.queryByText(/today’s focus/i)).not.toBeInTheDocument();
    });

    it('renders the dashboard even when the focus request fails', async () => {
        // Supplementary data: it must never take the page down with it.
        mockGetDashboardSummary.mockReset();
        mockGetDashboardSummary.mockResolvedValue(SUMMARY);
        mockGetTodaysFocus.mockRejectedValue(new Error('boom'));
        render(<Dashboard />);
        await waitFor(() => expect(mockGetDashboardSummary).toHaveBeenCalled());
        expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    });

    // ── Empty-state tiles (UX audit finding 2) ──

    // Built from SUMMARY so it carries every field the page needs; only the
    // has-ever-trained signals differ.
    const NEVER_TRAINED = { ...SUMMARY };

    it('hides the momentum and streak tiles for a user who has never trained', async () => {
        // The strip above already hides itself with no data. A tile that can
        // only say "0%" reads as a score the user has somehow already earned.
        mockGetDashboardSummary.mockReset();
        mockGetDashboardSummary.mockResolvedValue({ ...NEVER_TRAINED });

        render(<Dashboard />);
        await waitFor(() => expect(mockGetDashboardSummary).toHaveBeenCalled());

        expect(screen.queryByTestId('momentum-card')).not.toBeInTheDocument();
        expect(screen.queryByTestId('streak-card')).not.toBeInTheDocument();
    });

    it('keeps a genuine zero streak for someone who has trained before', async () => {
        // A broken streak is real information — hiding it would delete a fact,
        // not spare the user a meaningless one.
        mockGetDashboardSummary.mockReset();
        mockGetDashboardSummary.mockResolvedValue({
            ...NEVER_TRAINED,
            training_streak_days: 0,
            last_session_at: '2026-07-28T10:00:00Z',
        });

        render(<Dashboard />);
        await waitFor(() => expect(mockGetDashboardSummary).toHaveBeenCalled());
        expect(await screen.findByTestId('streak-card')).toBeInTheDocument();
    });

    it('still shows both tiles once there is data', async () => {
        mockGetDashboardSummary.mockReset();
        mockGetDashboardSummary.mockResolvedValue({
            ...NEVER_TRAINED,
            training_streak_days: 3,
            last_session_at: '2026-08-01T10:00:00Z',
            recent_form: {
                ...SUMMARY.recent_form,
                last_20_results: ['pass' as const, 'fail' as const, 'pass' as const],
                accuracy: 0.67, trend: 'up' as const, sample_size: 3,
                insufficient_data: false,
            },
        });

        render(<Dashboard />);
        expect(await screen.findByTestId('momentum-card')).toBeInTheDocument();
        expect(screen.getByTestId('streak-card')).toBeInTheDocument();
    });

    // ── Daily practice completion state ──

    it('passes completed_today=true from dashboard to HeroTrainCard', async () => {
        mockGetDashboardSummary.mockReset();
        mockGetDashboardSummary.mockResolvedValue({
            ...SUMMARY,
            schedule: { due_now: 3, due_in_4h: 0, next_review_at: null },
            daily_practice: { completed_today: true, completed_session_at: '2026-08-28T12:00:00Z' },
        });

        render(<Dashboard />);
        await waitFor(() => expect(screen.getByTestId('hero-card')).toHaveAttribute('data-completed-today', 'true'));
    });

    it('dashboard loads correctly when completed_today=false', async () => {
        mockGetDashboardSummary.mockReset();
        mockGetDashboardSummary.mockResolvedValue({
            ...SUMMARY,
            daily_practice: { completed_today: false, completed_session_at: null },
        });

        render(<Dashboard />);
        await waitFor(() => expect(screen.getByTestId('hero-card')).toBeInTheDocument());

        expect(screen.getByTestId('hero-card')).toHaveAttribute('data-completed-today', 'false');
    });
});
