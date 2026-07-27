import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import Engine from './Engine';

// The Opening Explorer hands a position over via `?fen=`, so a user can go from
// "I score badly in this line" to analysing it without retyping a FEN.

const SICILIAN_FEN = 'rnbqkbnr/pp1ppppp/8/2p5/4P3/5N2/PPPP1PPP/RNBQKB1R b KQkq - 1 2';
const STARTING_FEN = 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1';

let params = new URLSearchParams();

vi.mock('react-router-dom', () => ({
  Link: ({ children, to }: { children: React.ReactNode; to: string }) => <a href={to}>{children}</a>,
  useSearchParams: () => [params, vi.fn()],
}));

vi.mock('react-chessboard', () => ({
  Chessboard: () => <div data-testid="chessboard" />,
}));

vi.mock('../context/ChessUsernameContext', () => ({
  useChessUsername: () => ({ username: 'alice', setEditorOpen: vi.fn() }),
}));

vi.mock('../api', () => ({
  evaluateFen: vi.fn().mockResolvedValue({ bestMove: 'e2e4', eval: 0.2 }),
  getEngineStatus: vi.fn().mockResolvedValue({ available: false, message: 'off' }),
  ApiError: class extends Error {},
}));

vi.mock('../api/puzzles', () => ({ createManualPuzzle: vi.fn() }));

/** The FEN box is the page's own record of the position it loaded. */
const fenBox = () => screen.getByDisplayValue(/^[rnbqkpRNBQKP1-8/]+ [wb] /);

beforeEach(() => {
  vi.clearAllMocks();
  params = new URLSearchParams();
});

describe('Engine ?fen= deep link', () => {
  it('opens at the start position when no fen is given', async () => {
    render(<Engine />);

    await waitFor(() => expect(fenBox()).toHaveValue(STARTING_FEN));
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });

  it('opens at a position handed over by another page', async () => {
    params = new URLSearchParams({ fen: SICILIAN_FEN });
    render(<Engine />);

    await waitFor(() => expect(fenBox()).toHaveValue(SICILIAN_FEN));
  });

  it('explains an unusable link instead of rendering a broken board', async () => {
    params = new URLSearchParams({ fen: 'total nonsense' });
    render(<Engine />);

    await waitFor(() => expect(fenBox()).toHaveValue(STARTING_FEN));
    expect(screen.getByRole('alert')).toHaveTextContent(/invalid fen/i);
  });

  it('treats an empty fen param as no fen at all', async () => {
    params = new URLSearchParams({ fen: '' });
    render(<Engine />);

    await waitFor(() => expect(fenBox()).toHaveValue(STARTING_FEN));
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });
});
