import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import LibraryPuzzle from './LibraryPuzzle';

vi.mock('react-router-dom', () => ({
    useParams: () => ({ puzzleId: 'p1' }),
    Link: ({ children, to }: { children: React.ReactNode; to: string }) => <a href={to}>{children}</a>,
}));

vi.mock('../context/ChessUsernameContext', () => ({
    useChessUsername: () => ({ username: 'testplayer' }),
}));

const mockGetLibraryPuzzle = vi.fn();
vi.mock('../api/puzzles', () => ({
    getLibraryPuzzle: (...args: unknown[]) => mockGetLibraryPuzzle(...args),
    reviewPuzzle: vi.fn(),
}));

vi.mock('react-chessboard', () => ({
    Chessboard: () => <div data-testid="visual-board" />,
}));

function setOnline(value: boolean) {
    Object.defineProperty(navigator, 'onLine', { value, configurable: true });
}

describe('LibraryPuzzle offline handling', () => {
    beforeEach(() => {
        vi.clearAllMocks();
    });
    afterEach(() => {
        setOnline(true);
    });

    it('shows an offline affordance (not a generic error) when a fetch fails while offline', async () => {
        setOnline(false);
        mockGetLibraryPuzzle.mockRejectedValue(new Error('Network request failed'));

        render(<LibraryPuzzle />);

        await waitFor(() => {
            const alert = screen.getByRole('alert');
            expect(alert).toHaveTextContent(/offline/i);
        });
        // The raw network error message is not surfaced as the primary state.
        expect(screen.queryByText('Network request failed')).not.toBeInTheDocument();
        // ...and the offline card does not replace the page: the heading stays,
        // so heading navigation still identifies where the user is. The other
        // state branches are covered in LibraryPuzzle.states.test.tsx.
        expect(screen.getByRole('heading', { level: 1, name: 'Puzzle' })).toBeInTheDocument();
    });

    it('shows the standard error card when a fetch fails while online', async () => {
        setOnline(true);
        mockGetLibraryPuzzle.mockRejectedValue(new Error('Boom 500'));

        render(<LibraryPuzzle />);

        await waitFor(() => {
            expect(screen.getByText('Boom 500')).toBeInTheDocument();
        });
    });
});
