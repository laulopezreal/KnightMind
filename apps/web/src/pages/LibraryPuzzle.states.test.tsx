import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import LibraryPuzzle from './LibraryPuzzle';
import { ApiError } from '../api/core';

// Every state must keep the page's level-one heading. The loading and error
// branches used to render only a status block or an error card below the "Back
// to Library" link, leaving the page with no <h1> at all (axe:
// page-has-heading-one). The title isn't known until the fetch lands, so these
// states carry the same 'Puzzle' fallback the loaded view uses for an untitled
// puzzle.

vi.mock('react-router-dom', () => ({
    useParams: () => ({ puzzleId: 'p1' }),
    useSearchParams: () => [new URLSearchParams(), vi.fn()],
    Link: ({ children, to }: { children: React.ReactNode; to: string }) => <a href={to}>{children}</a>,
}));

vi.mock('../context/ChessUsernameContext', () => ({
    useChessUsername: () => ({ username: 'testplayer' }),
}));

const mockGetLibraryPuzzle = vi.fn();
vi.mock('../api/puzzles', () => ({
    getLibraryPuzzle: (...args: unknown[]) => mockGetLibraryPuzzle(...args),
    getPuzzleDiagnosis: vi.fn().mockResolvedValue(null),
    reviewPuzzle: vi.fn(),
}));

vi.mock('react-chessboard', () => ({ Chessboard: () => <div data-testid="visual-board" /> }));

function setOnline(value: boolean) {
    Object.defineProperty(navigator, 'onLine', { value, configurable: true });
}

describe('LibraryPuzzle data states', () => {
    beforeEach(() => {
        vi.clearAllMocks();
    });
    afterEach(() => setOnline(true));

    it('keeps a page h1 while loading', () => {
        mockGetLibraryPuzzle.mockReturnValue(new Promise(() => {}));

        render(<LibraryPuzzle />);

        expect(screen.getByRole('status')).toHaveTextContent(/loading puzzle/i);
        expect(screen.getByRole('heading', { level: 1, name: 'Puzzle' })).toBeInTheDocument();
    });

    it('keeps a page h1 in the error state', async () => {
        setOnline(true);
        mockGetLibraryPuzzle.mockRejectedValue(new Error('Boom 500'));

        render(<LibraryPuzzle />);

        await waitFor(() => expect(screen.getByText('Boom 500')).toBeInTheDocument());
        expect(screen.getByRole('heading', { level: 1, name: 'Puzzle' })).toBeInTheDocument();
    });

    // The offline branch is asserted alongside the rest of the offline
    // behaviour in LibraryPuzzle.offline.test.tsx, rather than a second time here.

    it('keeps a page h1 when the puzzle is not found', async () => {
        // A 404 is terminal (no Retry), so it renders a different branch than
        // the transient-error card above — it needs its own heading check.
        mockGetLibraryPuzzle.mockRejectedValue(new ApiError('Not here', 404));

        render(<LibraryPuzzle />);

        await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent(/puzzle not found/i));
        expect(screen.getByRole('heading', { level: 1, name: 'Puzzle' })).toBeInTheDocument();
    });
});
