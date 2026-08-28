/**
 * Regression test for the first-run celebration modal.
 *
 * It used to print the number of GAMES imported as though it were the number of
 * puzzles created ("40 puzzles generated from your games"), then redirect to a
 * dashboard showing a much smaller number — the very first factual claim a new
 * user ever sees, and it was wrong. The count is now the real delta in
 * puzzles_count, and is dropped entirely when it can't be determined.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, act } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import Home from './Home';

const mockNavigate = vi.fn();

vi.mock('react-router-dom', () => ({
    Link: ({ children, to, ...props }: { children: React.ReactNode; to: string;[key: string]: unknown }) => (
        <a href={to} {...props}>{children}</a>
    ),
    useNavigate: () => mockNavigate,
}));

vi.mock('../context/ChessUsernameContext', () => ({
    useChessUsername: () => ({ username: 'testplayer', setUsername: vi.fn(), setEditorOpen: vi.fn() }),
}));

vi.mock('../api', () => ({
    importChessComGames: vi.fn(),
    getImportStatus: vi.fn().mockResolvedValue({ last_imported_at: null, last_new_games: null }),
    validateChessComUser: vi.fn(),
    getUserStatus: vi.fn(),
    ApiError: class extends Error { detail?: string },
}));

vi.mock('../api/users', async () => {
    const barrel = await vi.importMock<typeof import('../api')>('../api');
    return {
        importChessComGames: barrel.importChessComGames,
        getImportStatus: barrel.getImportStatus,
        validateChessComUser: barrel.validateChessComUser,
        getUserStatus: barrel.getUserStatus,
    };
});

vi.mock('../api/core', () => ({ ApiError: class extends Error { detail?: string } }));

vi.mock('../api/puzzles', () => ({ generatePuzzles: vi.fn() }));

// Capture the polling callbacks so the test can drive the job to completion.
let jobOnSuccess: (() => void | Promise<void>) | undefined;
vi.mock('../hooks/useJobPolling', () => ({
    useJobPolling: (_id: string | null, opts?: { onSuccess?: () => void | Promise<void> }) => {
        jobOnSuccess = opts?.onSuccess;
        return { job: null, isPolling: false };
    },
}));

vi.mock('../components/Modal', () => ({
    Modal: ({ children, isOpen }: { children: React.ReactNode; isOpen: boolean }) =>
        isOpen ? <div>{children}</div> : null,
}));
vi.mock('../components/JobStatusCard', () => ({ JobStatusCard: () => null }));
vi.mock('../components/LoadingSpinner', () => ({ LoadingSpinner: () => <div>Loading...</div> }));

async function runImportThroughGeneration({ puzzlesAfter }: { puzzlesAfter: number | 'unavailable' }) {
    const api = await import('../api');
    const { generatePuzzles } = await import('../api/puzzles');

    const status = (games_count: number, puzzles_count: number) => ({
        username: 'testplayer',
        games_count,
        puzzles_count,
        due_count: 0,
        next_due_at: null,
        has_new_games: false,
    });

    vi.mocked(api.getUserStatus)
        .mockResolvedValueOnce(status(0, 0))   // initial page load: brand-new user
        .mockResolvedValueOnce(status(40, 0)); // refresh right after the import
    if (puzzlesAfter === 'unavailable') {
        vi.mocked(api.getUserStatus).mockRejectedValue(new Error('status unavailable'));
    } else {
        vi.mocked(api.getUserStatus).mockResolvedValue(status(40, puzzlesAfter));
    }

    vi.mocked(api.validateChessComUser).mockResolvedValue({ valid: true, username: 'testplayer' } as never);
    vi.mocked(api.importChessComGames).mockResolvedValue({ games_count: 40, new_games: 40 } as never);
    vi.mocked(generatePuzzles).mockResolvedValue({ job_id: 'job-1' } as never);

    const user = userEvent.setup();
    render(<Home />);

    await user.click(await screen.findByRole('button', { name: /import games/i }));
    await waitFor(() => expect(jobOnSuccess).toBeTypeOf('function'));
    await act(async () => { await jobOnSuccess!(); });
}

describe('Home onboarding celebration', () => {
    beforeEach(() => {
        vi.clearAllMocks();
        jobOnSuccess = undefined;
    });

    it('reports the puzzles actually created, not the games imported', async () => {
        await runImportThroughGeneration({ puzzlesAfter: 6 });

        expect(await screen.findByText('All Set!')).toBeInTheDocument();
        expect(screen.getByText('6 puzzles generated from your games.')).toBeInTheDocument();
        // 40 games were imported — that number must not be presented as puzzles.
        expect(screen.queryByText(/40 puzzles/)).not.toBeInTheDocument();
    });

    it('drops the number rather than guessing when the status is unavailable', async () => {
        await runImportThroughGeneration({ puzzlesAfter: 'unavailable' });

        expect(await screen.findByText('All Set!')).toBeInTheDocument();
        expect(screen.getByText('Your puzzles are ready.')).toBeInTheDocument();
    });
});
