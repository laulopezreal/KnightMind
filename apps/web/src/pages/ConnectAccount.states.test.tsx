import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import Dashboard from './Dashboard';
import Insights from './Insights';
import Library from './Library';
import Openings from './Openings';
import RatingInsights from './RatingInsights';
import { renderAt } from '../test/router';
import { setupMockLocalStorage } from '../test/helpers';

// Every account-dependent page must handle "no Chess.com username" the same
// way: explain in place. Dashboard, Insights and Openings used to redirect to
// Home, which announced nothing and made the sidebar links read as broken;
// Rating Insights and Library explained themselves but offered a button that
// could not work. One file so the five can't drift apart again.

const mockNavigate = vi.fn();

// Real router (the pages read the query string), with only navigation spied on
// — a redirect is exactly what these tests are here to rule out.
vi.mock('react-router-dom', async (importOriginal) => ({
    ...(await importOriginal<typeof import('react-router-dom')>()),
    useNavigate: () => mockNavigate,
}));

vi.mock('../context/ChessUsernameContext', () => ({
    useChessUsername: () => ({ username: '', setEditorOpen: vi.fn() }),
}));

// Any request here would be a request for *nobody* — the pages must not reach
// the network before an account exists, so every call fails the test loudly.
// vi.hoisted, because the factories below are lifted above this file's consts
// and read the spy while building their module object.
const { shouldNotFetch } = vi.hoisted(() => ({
    shouldNotFetch: vi.fn(() => Promise.reject(new Error('fetched without a username'))),
}));

vi.mock('../api', async (importOriginal) => ({
    ...(await importOriginal<typeof import('../api')>()),
    getOpenings: shouldNotFetch,
}));
vi.mock('../api/users', async (importOriginal) => ({
    ...(await importOriginal<typeof import('../api/users')>()),
    getDashboardSummary: shouldNotFetch,
    getTrickyPuzzles: shouldNotFetch,
    getMotifPerformance: shouldNotFetch,
    getMotifTrends: shouldNotFetch,
    getUserStatus: shouldNotFetch,
}));
vi.mock('../api/ratings', async (importOriginal) => ({
    ...(await importOriginal<typeof import('../api/ratings')>()),
    getRatingExplain: shouldNotFetch,
    getRatingHistory: shouldNotFetch,
}));
vi.mock('../api/sessions', async (importOriginal) => ({
    ...(await importOriginal<typeof import('../api/sessions')>()),
    getRecentSessions: shouldNotFetch,
}));
vi.mock('../api/puzzles', async (importOriginal) => ({
    ...(await importOriginal<typeof import('../api/puzzles')>()),
    getLibraryPuzzles: shouldNotFetch,
}));

// Heavy renderers the connect-account state never reaches.
vi.mock('../components/OpeningGraph', () => ({ OpeningGraph: () => <div /> }));
vi.mock('recharts', () => ({
    LineChart: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
    Line: () => <div />,
    XAxis: () => <div />,
    YAxis: () => <div />,
    Tooltip: () => <div />,
    ResponsiveContainer: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
    ReferenceDot: () => <div />,
}));

const PAGES = [
    { name: 'Dashboard', Component: Dashboard, heading: 'Dashboard', describes: /what's due, your streak/i },
    { name: 'Insights', Component: Insights, heading: 'Insights', describes: /tactical patterns/i },
    { name: 'Openings', Component: Openings, heading: 'Opening Explorer', describes: /opening graph/i },
    { name: 'Rating Insights', Component: RatingInsights, heading: 'Rating Insights', describes: /rating over time/i },
    { name: 'Library', Component: Library, heading: 'Library', describes: /puzzles generated from your own games/i },
] as const;

describe.each(PAGES)('$name without a Chess.com username', ({ Component, heading, describes }) => {
    beforeEach(() => {
        vi.clearAllMocks();
        setupMockLocalStorage();
    });

    it('explains itself in place instead of redirecting to Home', () => {
        renderAt(<Component />);

        expect(screen.getByText('Connect your Chess.com account')).toBeInTheDocument();
        expect(mockNavigate).not.toHaveBeenCalled();
    });

    it('keeps the user oriented by still rendering the page heading', () => {
        renderAt(<Component />);

        // Role + level, not bare text: "orienting the user" means the page keeps
        // a level-one heading for anyone navigating by headings, which is what
        // getByText would not have caught if a branch downgraded or dropped it.
        expect(screen.getByRole('heading', { level: 1, name: heading })).toBeInTheDocument();
    });

    it('puts the state title in the heading map, one level below the page heading', () => {
        renderAt(<Component />);

        // The title is the heading for what the page shows *instead of* its
        // content. Styled as a plain <p> it was invisible to heading
        // navigation, so a screen-reader user jumped from the page <h1>
        // straight past the only thing on the page. Level 2 because the test
        // above pins the <h1> — together they assert h1 -> h2, no level skipped.
        expect(
            screen.getByRole('heading', { level: 2, name: /connect your chess\.com account/i }),
        ).toBeInTheDocument();
    });

    it('offers a working route to connect an account', async () => {
        renderAt(<Component />);

        await userEvent.click(screen.getByRole('button', { name: 'Connect account' }));

        expect(mockNavigate).toHaveBeenCalledWith('/');
    });

    it('says what this particular page will show once connected', () => {
        renderAt(<Component />);

        // Not a generic "no data" — the copy names what the page is for, so the
        // state explains itself rather than reading as a dead end.
        expect(screen.getByText(describes)).toBeInTheDocument();
    });

    it('does not fetch before an account exists', () => {
        renderAt(<Component />);

        expect(shouldNotFetch).not.toHaveBeenCalled();
    });

    it('shows no loading state — there is nothing to wait for', () => {
        renderAt(<Component />);

        expect(screen.queryByRole('status')).not.toBeInTheDocument();
    });
});
