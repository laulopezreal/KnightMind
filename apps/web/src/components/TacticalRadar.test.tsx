import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { TacticalRadar } from './TacticalRadar';

// Mock recharts
vi.mock('recharts', () => ({
  Radar: () => <div data-testid="radar" />,
  RadarChart: ({ children }: { children: React.ReactNode }) => <div data-testid="radar-chart">{children}</div>,
  PolarGrid: () => <div />,
  PolarAngleAxis: () => <div />,
  PolarRadiusAxis: () => <div />,
  ResponsiveContainer: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
}));

describe('TacticalRadar', () => {
  const user = userEvent.setup();
  const onMotifClick = vi.fn();

  it('should show empty state when no motifs', () => {
    render(<TacticalRadar motifs={[]} onMotifClick={onMotifClick} />);

    expect(screen.getByText(/No motif data yet/)).toBeInTheDocument();
  });

  it('should show "not enough motifs" when fewer than 3', () => {
    const motifs = [
      { name: 'Fork', accuracy: 0.8, total_puzzles: 10, correct: 8 },
      { name: 'Pin', accuracy: 0.7, total_puzzles: 5, correct: 4 },
    ];

    render(<TacticalRadar motifs={motifs} onMotifClick={onMotifClick} />);

    expect(screen.getByText(/At least 3 different motifs/)).toBeInTheDocument();
  });

  it('should render radar chart with 3+ motifs', () => {
    const motifs = [
      { name: 'Fork', accuracy: 0.8, total_puzzles: 10, correct: 8 },
      { name: 'Pin', accuracy: 0.7, total_puzzles: 5, correct: 4 },
      { name: 'Skewer', accuracy: 0.6, total_puzzles: 8, correct: 5 },
    ];

    render(<TacticalRadar motifs={motifs} onMotifClick={onMotifClick} />);

    expect(screen.getByTestId('radar-chart')).toBeInTheDocument();
  });

  it('should show weakest motif with practice button', () => {
    const motifs = [
      { name: 'Fork', accuracy: 0.8, total_puzzles: 10, correct: 8 },
      { name: 'Pin', accuracy: 0.5, total_puzzles: 5, correct: 3 },
      { name: 'Skewer', accuracy: 0.9, total_puzzles: 8, correct: 7 },
    ];

    render(<TacticalRadar motifs={motifs} onMotifClick={onMotifClick} />);

    expect(screen.getByText(/Pin \(50%\)/)).toBeInTheDocument();
    expect(screen.getByText('Practice Pin Now')).toBeInTheDocument();
  });

  it('should call onMotifClick when practice button is clicked', async () => {
    const motifs = [
      { name: 'Fork', accuracy: 0.8, total_puzzles: 10, correct: 8 },
      { name: 'Pin', accuracy: 0.5, total_puzzles: 5, correct: 3 },
      { name: 'Skewer', accuracy: 0.9, total_puzzles: 8, correct: 7 },
    ];

    render(<TacticalRadar motifs={motifs} onMotifClick={onMotifClick} />);

    await user.click(screen.getByText('Practice Pin Now'));
    expect(onMotifClick).toHaveBeenCalledWith('Pin');
  });

  it('should show mastery celebration when all motifs >= 85%', () => {
    const motifs = [
      { name: 'Fork', accuracy: 0.9, total_puzzles: 10, correct: 9 },
      { name: 'Pin', accuracy: 0.85, total_puzzles: 5, correct: 4 },
      { name: 'Skewer', accuracy: 0.95, total_puzzles: 8, correct: 8 },
    ];

    render(<TacticalRadar motifs={motifs} onMotifClick={onMotifClick} />);

    expect(screen.getByText('All Motifs Mastered!')).toBeInTheDocument();
  });

  it('should render heading', () => {
    render(<TacticalRadar motifs={[]} onMotifClick={onMotifClick} />);

    expect(screen.getByText(/Tactical Vision/)).toBeInTheDocument();
  });
});
