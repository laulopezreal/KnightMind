import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { screen, waitFor, act } from '@testing-library/react';
import Openings from './Openings';
import { renderAt } from '../test/router';

// Dim 25: offline affordance + stale-response guard on username change.

let mockUsername = 'alice';
const mockNavigate = vi.fn();

vi.mock('react-router-dom', async (importOriginal) => ({
  ...(await importOriginal<typeof import('react-router-dom')>()),
  useNavigate: () => mockNavigate,
}));
vi.mock('../context/ChessUsernameContext', () => ({
    useChessUsername: () => ({ username: mockUsername }),
}));

const mockGetOpenings = vi.fn();
// Spread the real module and override only the call under test. A hand-listed
// mock breaks every time the page imports something new from `../api`.
vi.mock('../api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../api')>()),
  getOpenings: (...a: unknown[]) => mockGetOpenings(...a),
}));

vi.mock('../components/OpeningGraph', () => ({
    OpeningGraph: () => <div data-testid="opening-graph" />,
}));

// Matches the wire contract: the root is 'Start' (load-bearing — pathMoves
// filters on it) and win_rate is a percentage, not a fraction.
const MOCK_TREE = {
    move_san: 'Start', ply: 0, games_count: 42, wins: 20, draws: 12, losses: 10,
    win_rate: 48, eco: null, opening_name: null,
    children: [{
        move_san: 'e4', ply: 1, games_count: 42, wins: 20, draws: 12, losses: 10,
        win_rate: 48, eco: null, opening_name: null,
    }],
};

function setOnline(value: boolean) {
    Object.defineProperty(navigator, 'onLine', { value, configurable: true });
}

describe('Openings data states', () => {
    beforeEach(() => {
        vi.clearAllMocks();
        mockUsername = 'alice';
    });
    afterEach(() => setOnline(true));

    it('shows an offline affordance (not a generic error) when a fetch fails while offline', async () => {
        setOnline(false);
        mockGetOpenings.mockRejectedValue(new Error('Network request failed'));

        renderAt(<Openings />);

        await waitFor(() => {
            expect(screen.getByRole('alert')).toHaveTextContent(/offline/i);
        });
        expect(screen.queryByText('Network request failed')).not.toBeInTheDocument();
    });

    it('ignores a stale response from a superseded username', async () => {
        // A late *rejection* proves nothing: the page already keeps the tree on
        // any error and renders it as a polite status, so this passed with the
        // whole stale-request guard deleted. The real hazard is a late
        // *resolution* — alice's slow tree landing on top of bob's.
        setOnline(true);
        let resolveAlice!: (value: unknown) => void;
        mockGetOpenings
            .mockImplementationOnce(() => new Promise((res) => { resolveAlice = res; }))
            .mockResolvedValue({ ...MOCK_TREE, games_count: 77 });

        const { rerender } = renderAt(<Openings />);

        mockUsername = 'bob';
        rerender(<Openings />);
        await waitFor(() => expect(screen.getByTestId('opening-graph')).toBeInTheDocument());
        await waitFor(() => expect(screen.getByText('77')).toBeInTheDocument());

        // Alice's request finishes last and must not overwrite bob's tree.
        await act(async () => { resolveAlice({ ...MOCK_TREE, games_count: 11 }); });

        expect(screen.getByText('77')).toBeInTheDocument();
        expect(screen.queryByText('11')).not.toBeInTheDocument();
    });
});
