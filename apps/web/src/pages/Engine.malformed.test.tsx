import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import Engine from './Engine';
import { ApiError } from '../api/core';

// A malformed /engine/eval body used to reach formatEval's `.toFixed()` and take
// the whole page down through the error boundary. It must now land in the page's
// ordinary "evaluation failed" path instead.

vi.mock('react-router-dom', () => ({
  Link: ({ children, to }: { children: React.ReactNode; to: string }) => <a href={to}>{children}</a>,
  useSearchParams: () => [new URLSearchParams(), vi.fn()],
}));

vi.mock('react-chessboard', () => ({
  Chessboard: () => <div data-testid="chessboard" />,
}));

vi.mock('../context/ChessUsernameContext', () => ({
  useChessUsername: () => ({ username: 'alice', setEditorOpen: vi.fn() }),
}));

vi.mock('../api', async () => {
  const core = await vi.importActual<typeof import('../api/core')>('../api/core');
  return { evaluateFen: vi.fn(), getEngineStatus: vi.fn(), ApiError: core.ApiError };
});

vi.mock('../api/puzzles', () => ({ createManualPuzzle: vi.fn() }));

import { evaluateFen, getEngineStatus } from '../api';
const mockEvaluateFen = vi.mocked(evaluateFen);
const mockGetEngineStatus = vi.mocked(getEngineStatus);

const STARTING_FEN = 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1';
const ALT_FEN = 'rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1';

beforeEach(() => {
  vi.resetAllMocks();
  mockGetEngineStatus.mockResolvedValue({ available: true, message: 'Engine ready' });
});

/** Loading a new FEN is what triggers auto-evaluation — there is no evaluate button. */
async function evaluatePosition() {
  const user = userEvent.setup();
  fireEvent.change(screen.getByDisplayValue(STARTING_FEN), { target: { value: ALT_FEN } });
  await user.click(screen.getByText('Load'));
}

describe('Engine with a malformed evaluation response', () => {
  it('shows the failure instead of crashing the page', async () => {
    mockEvaluateFen.mockRejectedValue(
      new ApiError(
        'The analysis engine returned an unexpected response. Please try again.',
        502,
        'Malformed /engine/eval payload: {}',
      )
    );

    render(<Engine />);
    await evaluatePosition();

    await waitFor(
      () => expect(screen.getByText(/unexpected response/i)).toBeInTheDocument(),
      { timeout: 3000 }
    );
    // The page is still usable — the error boundary never took over.
    expect(screen.getByTestId('chessboard')).toBeInTheDocument();
    expect(screen.queryByText(/something went wrong/i)).not.toBeInTheDocument();
  });

  it('renders a well-formed evaluation normally', async () => {
    mockEvaluateFen.mockResolvedValue({ best_move_uci: 'e2e4', eval: 0.34 });

    render(<Engine />);
    await evaluatePosition();

    await waitFor(() => expect(screen.getByText('+0.34')).toBeInTheDocument(), { timeout: 3000 });
  });

  it('formats a zero evaluation rather than treating it as missing', async () => {
    // Guarding on truthiness rather than type would have swallowed this.
    mockEvaluateFen.mockResolvedValue({ best_move_uci: 'e2e4', eval: 0 });

    render(<Engine />);
    await evaluatePosition();

    await waitFor(() => expect(screen.getByText('+0.00')).toBeInTheDocument(), { timeout: 3000 });
  });
});
