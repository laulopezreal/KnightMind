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

vi.mock('../api/users', () => ({
    getMotifPerformance: (...a: unknown[]) => mockGetMotifPerformance(...a),
    getMotifTrends: (...a: unknown[]) => mockGetMotifTrends(...a),
    getTrickyPuzzles: (...a: unknown[]) => mockGetTrickyPuzzles(...a),
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
