import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import Openings from './Openings';

// Dim 25: offline affordance + stale-response guard on username change.

let mockUsername = 'alice';
const mockNavigate = vi.fn();

vi.mock('react-router-dom', () => ({ useNavigate: () => mockNavigate }));
vi.mock('../context/ChessUsernameContext', () => ({
    useChessUsername: () => ({ username: mockUsername }),
}));

const mockGetOpenings = vi.fn();
vi.mock('../api', () => ({
    getOpenings: (...a: unknown[]) => mockGetOpenings(...a),
    ApiError: class extends Error { detail?: string },
}));

vi.mock('../components/OpeningGraph', () => ({
    OpeningGraph: () => <div data-testid="opening-graph" />,
}));

const MOCK_TREE = {
    move_san: 'start', ply: 0, games_count: 42, wins: 20, draws: 12, losses: 10,
    win_rate: 0.48, children: [],
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

        render(<Openings />);

        await waitFor(() => {
            expect(screen.getByRole('alert')).toHaveTextContent(/offline/i);
        });
        expect(screen.queryByText('Network request failed')).not.toBeInTheDocument();
    });

    it('ignores a stale response from a superseded username', async () => {
        setOnline(true);
        let rejectAlice!: (reason?: unknown) => void;
        mockGetOpenings
            .mockImplementationOnce(() => new Promise((_, rej) => { rejectAlice = rej; }))
            .mockResolvedValue(MOCK_TREE);

        const { rerender } = render(<Openings />);

        mockUsername = 'bob';
        rerender(<Openings />);
        await waitFor(() => expect(screen.getByTestId('opening-graph')).toBeInTheDocument());

        // A superseded response failing must not clear the newer tree or show an error.
        rejectAlice(new Error('alice stale error'));
        await new Promise((r) => setTimeout(r, 20));

        expect(screen.queryByRole('alert')).not.toBeInTheDocument();
        expect(screen.getByTestId('opening-graph')).toBeInTheDocument();
    });
});
