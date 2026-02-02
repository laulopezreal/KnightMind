import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import Engine from './Engine';
import { evaluateFen, getEngineStatus } from '../api';

// Mock dependencies
vi.mock('../api', () => ({
  evaluateFen: vi.fn(),
  getEngineStatus: vi.fn(),
}));

vi.mock('react-router-dom', () => ({
  Link: ({ children, ...props }: { children: React.ReactNode; [key: string]: unknown }) => <a {...props}>{children}</a>,
}));

vi.mock('react-chessboard', () => ({
  Chessboard: ({ options }: { options: unknown }) => (
    <div data-testid="chessboard" data-options={JSON.stringify(options)}>
      Chessboard
    </div>
  ),
}));

const mockEvaluateFen = vi.mocked(evaluateFen);
const mockGetEngineStatus = vi.mocked(getEngineStatus);

describe('Engine - Clue Functionality', () => {
  const user = userEvent.setup();

  beforeEach(() => {
    vi.clearAllMocks();
    mockGetEngineStatus.mockResolvedValue({ available: true });
    mockEvaluateFen.mockResolvedValue({ best_move_uci: 'e2e4', eval: 0.5 });
  });

  describe('Clue Stage Transitions', () => {
    it('should start with clueStage = 0', async () => {
      render(<Engine />);

      // Evaluate a position first
      const evaluateButton = screen.getByRole('button', { name: /Evaluate Position/i });
      await user.click(evaluateButton);

      await waitFor(() => {
        expect(screen.getByText('Clue')).toBeInTheDocument();
      });
    });

    it('should transition from stage 0 to stage 1 on first clue click', async () => {
      render(<Engine />);

      // Evaluate and get evaluation
      const evaluateButton = screen.getByRole('button', { name: /Evaluate Position/i });
      await user.click(evaluateButton);

      await waitFor(() => {
        expect(screen.getByText('Clue')).toBeInTheDocument();
      });

      // Click clue button
      const clueButton = screen.getByText('Clue');
      await user.click(clueButton);

      await waitFor(() => {
        expect(screen.getByText('Reveal squares')).toBeInTheDocument();
      });
    });

    it('should transition from stage 1 to stage 2 on second clue click', async () => {
      render(<Engine />);

      // Evaluate and get evaluation
      const evaluateButton = screen.getByRole('button', { name: /Evaluate Position/i });
      await user.click(evaluateButton);

      await waitFor(() => {
        expect(screen.getByText('Clue')).toBeInTheDocument();
      });

      // Click clue twice
      const clueButton = screen.getByText('Clue');
      await user.click(clueButton);

      await waitFor(() => {
        const revealButton = screen.getByText('Reveal squares');
        expect(revealButton).toBeInTheDocument();
      });

      await user.click(screen.getByText('Reveal squares'));

      await waitFor(() => {
        expect(screen.getByText('Clue used')).toBeInTheDocument();
      });
    });

    it('should reach final stage and be disabled after two clicks', async () => {
      render(<Engine />);

      // Evaluate and get evaluation
      const evaluateButton = screen.getByRole('button', { name: /Evaluate Position/i });
      await user.click(evaluateButton);

      await waitFor(() => {
        expect(screen.getByText('Clue')).toBeInTheDocument();
      });

      // Click clue three times
      let clueButton = screen.getByText('Clue');
      await user.click(clueButton);

      await waitFor(() => {
        clueButton = screen.getByText('Reveal squares');
        expect(clueButton).toBeInTheDocument();
      });

      await user.click(clueButton);

      await waitFor(() => {
        clueButton = screen.getByText('Clue used');
        expect(clueButton).toBeInTheDocument();
        expect(clueButton).toBeDisabled();
      });
    });
  });

  describe('Button States', () => {
    it('should disable clue button in stage 2', async () => {
      render(<Engine />);

      // Evaluate and get evaluation
      const evaluateButton = screen.getByRole('button', { name: /Evaluate Position/i });
      await user.click(evaluateButton);

      await waitFor(() => {
        expect(screen.getByText('Clue')).toBeInTheDocument();
      });

      // Click clue twice to reach stage 2
      let clueButton = screen.getByText('Clue');
      await user.click(clueButton);

      await waitFor(() => {
        clueButton = screen.getByText('Reveal squares');
        expect(clueButton).toBeInTheDocument();
      });

      await user.click(clueButton);

      await waitFor(() => {
        clueButton = screen.getByText('Clue used');
        expect(clueButton).toBeDisabled();
        expect(clueButton).toHaveClass('opacity-50');
      });
    });

    it('should enable clue button in stages 0 and 1', async () => {
      render(<Engine />);

      // Evaluate and get evaluation
      const evaluateButton = screen.getByRole('button', { name: /Evaluate Position/i });
      await user.click(evaluateButton);

      await waitFor(() => {
        const clueButton = screen.getByText('Clue');
        expect(clueButton).not.toBeDisabled();
        expect(clueButton).not.toHaveClass('opacity-50');
      });

      // Click once to stage 1
      await user.click(screen.getByText('Clue'));

      await waitFor(() => {
        const clueButton = screen.getByText('Reveal squares');
        expect(clueButton).not.toBeDisabled();
        expect(clueButton).not.toHaveClass('opacity-50');
      });
    });
  });

  describe('Piece Name Display', () => {
    it('should display correct piece name in stage 1', async () => {
      render(<Engine />);

      // Evaluate and get evaluation
      const evaluateButton = screen.getByRole('button', { name: /Evaluate Position/i });
      await user.click(evaluateButton);

      await waitFor(() => {
        expect(screen.getByText('Clue')).toBeInTheDocument();
      });

      // Click clue to stage 1
      await user.click(screen.getByText('Clue'));

      await waitFor(() => {
        expect(screen.getByText('Move the pawn')).toBeInTheDocument();
      });
    });

    it('should show default hint when piece cannot be determined', async () => {
      mockEvaluateFen.mockResolvedValue({ best_move_uci: 'x9y0', eval: 0.5 });

      render(<Engine />);

      const evaluateButton = screen.getByRole('button', { name: /Evaluate Position/i });
      await user.click(evaluateButton);

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
      render(<Engine />);

      const evaluateButton = screen.getByRole('button', { name: /Evaluate Position/i });
      await user.click(evaluateButton);

      await waitFor(() => {
        expect(screen.getByText('Clue')).toBeInTheDocument();
      });

      await user.click(screen.getByText('Clue'));

      await waitFor(() => {
        const chessboard = screen.getByTestId('chessboard');
        const options = JSON.parse(chessboard.getAttribute('data-options') || '{}');

        expect(options.squareStyles).toEqual({
          e2: { backgroundColor: 'rgba(255, 235, 59, 0.45)' }
        });
      });
    });

    it('should highlight both source and target squares in stage 2', async () => {
      render(<Engine />);

      const evaluateButton = screen.getByRole('button', { name: /Evaluate Position/i });
      await user.click(evaluateButton);

      await waitFor(() => {
        expect(screen.getByText('Clue')).toBeInTheDocument();
      });

      // Click clue twice
      await user.click(screen.getByText('Clue'));

      await waitFor(() => {
        const chessboard = screen.getByTestId('chessboard');
        const options = JSON.parse(chessboard.getAttribute('data-options') || '{}');

        expect(options.squareStyles).toEqual({
          e2: { backgroundColor: 'rgba(255, 235, 59, 0.45)' }
        });
      });

      await user.click(screen.getByText('Reveal squares'));

      await waitFor(() => {
        const chessboard = screen.getByTestId('chessboard');
        const options = JSON.parse(chessboard.getAttribute('data-options') || '{}');

        expect(options.squareStyles).toEqual({
          e2: { backgroundColor: 'rgba(255, 235, 59, 0.45)' },
          e4: { backgroundColor: 'rgba(255, 235, 59, 0.45)' }
        });
      });
    });

    it('should clear highlighting when clues are reset', async () => {
      render(<Engine />);

      const evaluateButton = screen.getByRole('button', { name: /Evaluate Position/i });
      await user.click(evaluateButton);

      await waitFor(() => {
        expect(screen.getByText('Clue')).toBeInTheDocument();
      });

      // Click clue to stage 1
      await user.click(screen.getByText('Clue'));

      await waitFor(() => {
        const chessboard = screen.getByTestId('chessboard');
        const options = JSON.parse(chessboard.getAttribute('data-options') || '{}');
        expect(Object.keys(options.squareStyles || {})).toHaveLength(1);
      });

      // Click again to stage 2
      await user.click(screen.getByText('Reveal squares'));

      await waitFor(() => {
        const chessboard = screen.getByTestId('chessboard');
        const options = JSON.parse(chessboard.getAttribute('data-options') || '{}');
        expect(Object.keys(options.squareStyles || {})).toHaveLength(2);
      });

      // Click reset position
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
    it('should disable clue button when no evaluation is available', async () => {
      render(<Engine />);

      // Clue button should not be present without evaluation
      expect(screen.queryByText('Clue')).not.toBeInTheDocument();
    });

    it('should reset clues when new positions are loaded', async () => {
      render(<Engine />);

      // Evaluate position
      const evaluateButton = screen.getByRole('button', { name: /Evaluate Position/i });
      await user.click(evaluateButton);

      await waitFor(() => {
        expect(screen.getByText('Clue')).toBeInTheDocument();
      });

      // Use clue
      await user.click(screen.getByText('Clue'));

      await waitFor(() => {
        expect(screen.getByText('Reveal squares')).toBeInTheDocument();
      });

      // Load new position
      const fenInput = screen.getByDisplayValue('rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1');
      await user.clear(fenInput);
      await user.type(fenInput, 'rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq e3 0 1');

      const loadButton = screen.getByText('Load');
      await user.click(loadButton);

      // Clue should be reset
      await waitFor(() => {
        expect(screen.queryByText('Reveal squares')).not.toBeInTheDocument();
      });
    });

    it('should reset clues when making moves', async () => {
      render(<Engine />);

      // Evaluate position
      const evaluateButton = screen.getByRole('button', { name: /Evaluate Position/i });
      await user.click(evaluateButton);

      await waitFor(() => {
        expect(screen.getByText('Clue')).toBeInTheDocument();
      });

      // Use clue
      await user.click(screen.getByText('Clue'));

      await waitFor(() => {
        expect(screen.getByText('Reveal squares')).toBeInTheDocument();
      });

      // Make a move by clicking load (simulates position change)
      const loadButton = screen.getByText('Load');
      await user.click(loadButton);

      // Clue should be reset
      await waitFor(() => {
        expect(screen.queryByText('Reveal squares')).not.toBeInTheDocument();
      });
    });
  });

  describe('Error Handling', () => {
    it('should handle undefined bestMove gracefully', async () => {
      mockEvaluateFen.mockResolvedValue({ best_move_uci: undefined as string | undefined, eval: 0.5 });

      render(<Engine />);

      const evaluateButton = screen.getByRole('button', { name: /Evaluate Position/i });
      await user.click(evaluateButton);

      await waitFor(() => {
        expect(screen.getByText('Clue')).toBeInTheDocument();
      });

      // Clicking clue should not crash
      await user.click(screen.getByText('Clue'));

      // Should still be in stage 0
      await waitFor(() => {
        expect(screen.getByText('Clue')).toBeInTheDocument();
      });
    });

    it('should handle malformed UCI strings gracefully', async () => {
      mockEvaluateFen.mockResolvedValue({ best_move_uci: 'invalid', eval: 0.5 });

      render(<Engine />);

      const evaluateButton = screen.getByRole('button', { name: /Evaluate Position/i });
      await user.click(evaluateButton);

      await waitFor(() => {
        expect(screen.getByText('Clue')).toBeInTheDocument();
      });

      await user.click(screen.getByText('Clue'));

      await waitFor(() => {
        expect(screen.getByText('Move the correct piece')).toBeInTheDocument();
      });
    });

    it('should reset clues on evaluation errors', async () => {
      // First successful evaluation
      mockEvaluateFen.mockResolvedValueOnce({ best_move_uci: 'e2e4', eval: 0.5 });

      render(<Engine />);

      const evaluateButton = screen.getByRole('button', { name: /Evaluate Position/i });
      await user.click(evaluateButton);

      await waitFor(() => {
        expect(screen.getByText('Clue')).toBeInTheDocument();
      });

      // Use clue
      await user.click(screen.getByText('Clue'));

      await waitFor(() => {
        expect(screen.getByText('Reveal squares')).toBeInTheDocument();
      });

      // Second evaluation fails
      mockEvaluateFen.mockRejectedValueOnce(new Error('API Error'));

      await user.click(evaluateButton);

      await waitFor(() => {
        expect(screen.queryByText('Reveal squares')).not.toBeInTheDocument();
      });
    });
  });

  describe('Accessibility', () => {
    it('should have proper button roles and labels', async () => {
      render(<Engine />);

      const evaluateButton = screen.getByRole('button', { name: /Evaluate Position/i });
      await user.click(evaluateButton);

      await waitFor(() => {
        const clueButton = screen.getByText('Clue');
        expect(clueButton).toHaveAttribute('type', 'button');
      });
    });

    it('should maintain keyboard focus during clue transitions', async () => {
      render(<Engine />);

      const evaluateButton = screen.getByRole('button', { name: /Evaluate Position/i });
      await user.click(evaluateButton);

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
});