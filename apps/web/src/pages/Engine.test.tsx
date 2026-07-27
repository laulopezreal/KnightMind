import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import Engine from './Engine';
import { ChessUsernameProvider } from '../context/ChessUsernameContext';

// Mock dependencies
vi.mock('../api', () => ({
  evaluateFen: vi.fn(),
  getEngineStatus: vi.fn(),
  ApiError: class extends Error { detail?: string },
}));

vi.mock('react-router-dom', () => ({
  Link: ({ children, to, ...props }: { children: React.ReactNode; to: string; [key: string]: unknown }) => (
    <a href={to} {...props}>{children}</a>
  ),
}));

vi.mock('react-chessboard', () => ({
  Chessboard: ({ options }: { options: Record<string, unknown> }) => (
    <div data-testid="chessboard" data-options={JSON.stringify(options ?? {})}>
      Chessboard
    </div>
  ),
}));

vi.mock('chess.js', () => {
  class MockChess {
    private currentFen: string;
    constructor(fen?: string) {
      // Mirror chess.js: reject obviously-invalid FEN so the component's
      // validation/catch path is exercised. Real FENs always contain a board
      // rank separator ('/'); the fixtures used elsewhere here do.
      if (fen !== undefined && !fen.includes('/')) {
        throw new Error('Invalid FEN');
      }
      this.currentFen = fen || 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1';
    }
    fen() { return this.currentFen; }
    get(square: string) {
      // Starting position: e2 has a white pawn
      if (square === 'e2') return { type: 'p', color: 'w' };
      return null;
    }
    move() { return { san: 'e4' }; }
    board() { return []; }
  }
  return { Chess: MockChess };
});

// Import mocked functions after vi.mock
import { evaluateFen, getEngineStatus } from '../api';
const mockEvaluateFen = vi.mocked(evaluateFen);
const mockGetEngineStatus = vi.mocked(getEngineStatus);

const STARTING_FEN = 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1';
const ALT_FEN = 'rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1';

function renderEngine() {
  return render(
    <ChessUsernameProvider>
      <Engine />
    </ChessUsernameProvider>
  );
}

describe('Engine - Clue Functionality', () => {
  let user: ReturnType<typeof userEvent.setup>;

  beforeEach(() => {
    user = userEvent.setup();
    vi.resetAllMocks();
    mockGetEngineStatus.mockResolvedValue({ available: true, message: 'Engine ready' });
    mockEvaluateFen.mockResolvedValue({ best_move_uci: 'e2e4', eval: 0.5 });
  });

  // Helper: load a new FEN to trigger auto-evaluation (no manual evaluate button in new UI).
  // Uses fireEvent.change for the FEN input because userEvent.type would type 56 chars
  // one-by-one, causing excessive re-renders with no additional behavioral coverage.
  async function evaluatePosition() {
    const fenInput = screen.getByDisplayValue(STARTING_FEN);
    fireEvent.change(fenInput, { target: { value: ALT_FEN } });
    await user.click(screen.getByText('Load'));

    // Wait for auto-eval (500ms debounce + mock resolution) to complete
    await waitFor(() => {
      expect(screen.getByText('Clue')).toBeInTheDocument();
    }, { timeout: 2000 });
  }

  describe('Clue Stage Transitions', () => {
    it('should start with clueStage = 0', async () => {
      renderEngine();

      await evaluatePosition();

      await waitFor(() => {
        expect(screen.getByText('Clue')).toBeInTheDocument();
      });
    });

    it('should transition from stage 0 to stage 1 on first clue click', async () => {
      renderEngine();

      await evaluatePosition();

      await waitFor(() => {
        expect(screen.getByText('Clue')).toBeInTheDocument();
      });

      await user.click(screen.getByText('Clue'));

      await waitFor(() => {
        expect(screen.getByText('Reveal squares')).toBeInTheDocument();
      });
    });

    it('should transition from stage 1 to stage 2 on second clue click', async () => {
      renderEngine();

      await evaluatePosition();

      await waitFor(() => {
        expect(screen.getByText('Clue')).toBeInTheDocument();
      });

      await user.click(screen.getByText('Clue'));

      await waitFor(() => {
        expect(screen.getByText('Reveal squares')).toBeInTheDocument();
      });

      await user.click(screen.getByText('Reveal squares'));

      await waitFor(() => {
        expect(screen.getByText('Hide clues and reset')).toBeInTheDocument();
      });
    });

    it('should cycle back to stage 0 from stage 2 on click', async () => {
      renderEngine();

      await evaluatePosition();

      await user.click(screen.getByText('Clue'));

      await waitFor(() => {
        expect(screen.getByText('Reveal squares')).toBeInTheDocument();
      });

      await user.click(screen.getByText('Reveal squares'));

      await waitFor(() => {
        expect(screen.getByText('Hide clues and reset')).toBeInTheDocument();
      });

      await user.click(screen.getByText('Hide clues and reset'));

      await waitFor(() => {
        expect(screen.getByText('Clue')).toBeInTheDocument();
        // Verify square highlights are also cleared
        const chessboard = screen.getByTestId('chessboard');
        const options = JSON.parse(chessboard.getAttribute('data-options') || '{}');
        expect(options.squareStyles).toEqual({});
      });
    });
  });

  describe('Button States', () => {
    it('should not disable clue button in any stage', async () => {
      renderEngine();

      await evaluatePosition();

      // Stage 0
      await waitFor(() => {
        expect(screen.getByText('Clue')).not.toBeDisabled();
      });

      // Stage 1
      await user.click(screen.getByText('Clue'));
      await waitFor(() => {
        expect(screen.getByText('Reveal squares')).not.toBeDisabled();
      });

      // Stage 2 — still enabled (resets on click)
      await user.click(screen.getByText('Reveal squares'));
      await waitFor(() => {
        expect(screen.getByText('Hide clues and reset')).not.toBeDisabled();
      });
    });
  });

  describe('Piece Name Display', () => {
    it('should show hint prompt in stage 0', async () => {
      renderEngine();

      await evaluatePosition();

      await waitFor(() => {
        expect(screen.getByText('Tap for a small hint.')).toBeInTheDocument();
      });
    });

    it('should display correct piece name in stage 1', async () => {
      renderEngine();

      await evaluatePosition();

      await waitFor(() => {
        expect(screen.getByText('Clue')).toBeInTheDocument();
      });

      await user.click(screen.getByText('Clue'));

      await waitFor(() => {
        expect(screen.getByText('Move the pawn')).toBeInTheDocument();
      });
    });

    it('should not show piece name in stage 2', async () => {
      renderEngine();

      await evaluatePosition();

      await user.click(screen.getByText('Clue'));

      await waitFor(() => {
        expect(screen.getByText('Move the pawn')).toBeInTheDocument();
      });

      await user.click(screen.getByText('Reveal squares'));

      await waitFor(() => {
        expect(screen.getByText('Hide clues and reset')).toBeInTheDocument();
        expect(screen.queryByText('Move the pawn')).not.toBeInTheDocument();
      });
    });

    it('should show default hint when piece cannot be determined', async () => {
      mockEvaluateFen.mockResolvedValue({ best_move_uci: 'x9y0', eval: 0.5 });

      renderEngine();

      await evaluatePosition();

      await waitFor(() => {
        expect(screen.getByText('Clue')).toBeInTheDocument();
      });

      await user.click(screen.getByText('Clue'));

      await waitFor(() => {
        expect(screen.getByText('Move the correct piece')).toBeInTheDocument();
      });
    });
  });

  describe('Board Highlighting', () => {
    it('should highlight only source square in stage 1', async () => {
      renderEngine();

      await evaluatePosition();

      await waitFor(() => {
        expect(screen.getByText('Clue')).toBeInTheDocument();
      });

      await user.click(screen.getByText('Clue'));

      await waitFor(() => {
        const chessboard = screen.getByTestId('chessboard');
        const options = JSON.parse(chessboard.getAttribute('data-options') || '{}');
        expect(options.squareStyles).toEqual({
          e2: { backgroundColor: 'rgba(255, 235, 59, 0.45)' },
        });
      });
    });

    it('should highlight both source and target squares in stage 2', async () => {
      renderEngine();

      await evaluatePosition();

      await waitFor(() => {
        expect(screen.getByText('Clue')).toBeInTheDocument();
      });

      await user.click(screen.getByText('Clue'));

      await waitFor(() => {
        expect(screen.getByText('Reveal squares')).toBeInTheDocument();
      });

      await user.click(screen.getByText('Reveal squares'));

      await waitFor(() => {
        const chessboard = screen.getByTestId('chessboard');
        const options = JSON.parse(chessboard.getAttribute('data-options') || '{}');
        expect(options.squareStyles).toEqual({
          e2: { backgroundColor: 'rgba(255, 235, 59, 0.45)' },
          e4: { backgroundColor: 'rgba(255, 235, 59, 0.45)' },
        });
      });
    });

    it('should clear highlighting when position is reset', async () => {
      renderEngine();

      await evaluatePosition();

      await user.click(screen.getByText('Clue'));

      await waitFor(() => {
        const chessboard = screen.getByTestId('chessboard');
        const options = JSON.parse(chessboard.getAttribute('data-options') || '{}');
        expect(Object.keys(options.squareStyles || {})).toHaveLength(1);
      });

      await user.click(screen.getByText('Reveal squares'));

      await waitFor(() => {
        const chessboard = screen.getByTestId('chessboard');
        const options = JSON.parse(chessboard.getAttribute('data-options') || '{}');
        expect(Object.keys(options.squareStyles || {})).toHaveLength(2);
      });

      // Reset position clears everything
      const resetButton = screen.getByText('Reset Position');
      await user.click(resetButton);

      await waitFor(() => {
        const chessboard = screen.getByTestId('chessboard');
        const options = JSON.parse(chessboard.getAttribute('data-options') || '{}');
        expect(options.squareStyles).toEqual({});
      });
    });
  });

  describe('UI Integration', () => {
    it('should not show clue button when no evaluation is available', () => {
      renderEngine();

      expect(screen.queryByText('Clue')).not.toBeInTheDocument();
    });

    it('should show waiting text when no evaluation', async () => {
      renderEngine();

      await waitFor(() => {
        expect(screen.getByText('Waiting for position')).toBeInTheDocument();
        expect(screen.getByText('Set or paste a position to analyze.')).toBeInTheDocument();
      });
    });

    it('should show loading text while evaluating', async () => {
      // Make evaluateFen hang indefinitely so we can observe the loading state
      mockEvaluateFen.mockReturnValue(new Promise(() => {}));

      renderEngine();

      // Load a new FEN to trigger auto-evaluation
      const fenInput = screen.getByDisplayValue(STARTING_FEN);
      fireEvent.change(fenInput, { target: { value: ALT_FEN } });
      await user.click(screen.getByText('Load'));

      await waitFor(() => {
        expect(screen.getByText(/Waiting for Stockfish output/)).toBeInTheDocument();
      }, { timeout: 2000 });
    });

    it('should reset clues when new positions are loaded', async () => {
      renderEngine();

      await evaluatePosition();

      await waitFor(() => {
        expect(screen.getByText('Clue')).toBeInTheDocument();
      });

      await user.click(screen.getByText('Clue'));

      await waitFor(() => {
        expect(screen.getByText('Reveal squares')).toBeInTheDocument();
      });

      // Load new FEN position
      const fenInput = screen.getByDisplayValue(ALT_FEN);
      fireEvent.change(fenInput, { target: { value: STARTING_FEN } });
      await user.click(screen.getByText('Load'));

      // Clue should be reset (handleFenSubmit resets clueStage to 0)
      await waitFor(() => {
        expect(screen.queryByText('Reveal squares')).not.toBeInTheDocument();
      });
    });

    it('should reset clues when reloading position', async () => {
      renderEngine();

      await evaluatePosition();

      await user.click(screen.getByText('Clue'));

      await waitFor(() => {
        expect(screen.getByText('Reveal squares')).toBeInTheDocument();
      });

      // Click load again (reloads same FEN, resets clueStage)
      const loadButton = screen.getByText('Load');
      await user.click(loadButton);

      await waitFor(() => {
        expect(screen.queryByText('Reveal squares')).not.toBeInTheDocument();
      });
    });

    it('explains solution line expects a full UCI PV', async () => {
      renderEngine();

      await evaluatePosition();

      expect(
        await screen.findByText('UCI PV, include opponent replies, e.g. e2e4 e7e5 g1f3')
      ).toBeInTheDocument();
      expect(screen.getByLabelText('Solution line')).toHaveAttribute(
        'aria-describedby',
        'save-solution-help'
      );
    });
  });

  describe('Error Handling', () => {
    it('should handle empty bestMove gracefully', async () => {
      mockEvaluateFen.mockResolvedValue({ best_move_uci: '', eval: 0.5 });

      renderEngine();

      // Load position to trigger eval
      const fenInput = screen.getByDisplayValue(STARTING_FEN);
      fireEvent.change(fenInput, { target: { value: ALT_FEN } });
      await user.click(screen.getByText('Load'));

      // Wait for evaluation (empty bestMove still sets evaluation)
      await waitFor(() => {
        expect(screen.getByText('Clue')).toBeInTheDocument();
      }, { timeout: 2000 });

      // Clicking Clue should not transition (handleClue returns early for empty bestMove)
      await user.click(screen.getByText('Clue'));

      // Should still be in stage 0
      await waitFor(() => {
        expect(screen.getByText('Clue')).toBeInTheDocument();
        expect(screen.getByText('Tap for a small hint.')).toBeInTheDocument();
      });
    });

    it('should handle malformed UCI strings gracefully', async () => {
      mockEvaluateFen.mockResolvedValue({ best_move_uci: 'invalid', eval: 0.5 });

      renderEngine();

      await evaluatePosition();

      await waitFor(() => {
        expect(screen.getByText('Clue')).toBeInTheDocument();
      });

      await user.click(screen.getByText('Clue'));

      await waitFor(() => {
        expect(screen.getByText('Move the correct piece')).toBeInTheDocument();
      });
    });

    it('should reset clues on evaluation errors', async () => {
      mockEvaluateFen.mockResolvedValueOnce({ best_move_uci: 'e2e4', eval: 0.5 });

      renderEngine();

      await evaluatePosition();

      await waitFor(() => {
        expect(screen.getByText('Clue')).toBeInTheDocument();
      });

      await user.click(screen.getByText('Clue'));

      await waitFor(() => {
        expect(screen.getByText('Reveal squares')).toBeInTheDocument();
      });

      // Next evaluation fails
      mockEvaluateFen.mockRejectedValueOnce(new Error('API Error'));

      // Load a different position to trigger another eval
      const fenInput = screen.getByDisplayValue(ALT_FEN);
      fireEvent.change(fenInput, { target: { value: STARTING_FEN } });
      await user.click(screen.getByText('Load'));

      await waitFor(() => {
        expect(screen.queryByText('Reveal squares')).not.toBeInTheDocument();
      });
    });
  });

  describe('Accessibility', () => {
    it('should have proper button roles and labels', async () => {
      renderEngine();

      await evaluatePosition();

      await waitFor(() => {
        const clueButton = screen.getByText('Clue');
        expect(clueButton).toHaveAttribute('type', 'button');
      });
    });

    it('should maintain keyboard focus during clue transitions', async () => {
      renderEngine();

      await evaluatePosition();

      await waitFor(() => {
        expect(screen.getByText('Clue')).toBeInTheDocument();
      });

      const clueButton = screen.getByText('Clue');
      clueButton.focus();

      await user.click(clueButton);

      await waitFor(() => {
        const newClueButton = screen.getByText('Reveal squares');
        expect(document.activeElement).toBe(newClueButton);
      });
    });
  });

  describe('Invalid FEN accessibility', () => {
    it('exposes the FEN validation error to assistive tech', async () => {
      renderEngine();

      const fenInput = screen.getByDisplayValue(STARTING_FEN);
      fireEvent.change(fenInput, { target: { value: 'not a valid fen' } });
      fireEvent.click(screen.getByText('Load'));

      // The error must be an alert (announced) and programmatically tied to the
      // input, not merely visible — a sighted-only error is an a11y defect.
      const alert = await screen.findByText('Invalid FEN string');
      expect(alert).toHaveAttribute('role', 'alert');
      expect(fenInput).toHaveAttribute('aria-invalid', 'true');
      expect(fenInput).toHaveAttribute('aria-describedby', alert.id);
    });
  });
});
