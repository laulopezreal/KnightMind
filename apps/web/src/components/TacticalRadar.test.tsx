import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { TacticalRadar } from './TacticalRadar';
import type { MotifPerformance } from '../api/users';

// Mock recharts
vi.mock('recharts', () => ({
  Radar: () => <div data-testid="radar" />,
  RadarChart: ({ children }: { children: React.ReactNode }) => <div data-testid="radar-chart">{children}</div>,
  PolarGrid: () => <div />,
  PolarAngleAxis: () => <div />,
  PolarRadiusAxis: () => <div />,
  ResponsiveContainer: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
}));

// Factory with reliable-sample defaults; override per test as needed.
const m = (over: Partial<MotifPerformance> & { name: string; accuracy: number; rank: MotifPerformance['rank'] }): MotifPerformance => ({
  total_puzzles: 10,
  passed: Math.round(over.accuracy * 10),
  attempts: 10,
  insufficient_data: false,
  ...over,
});

describe('TacticalRadar', () => {
  const user = userEvent.setup();
  const onMotifClick = vi.fn();

  it('should show empty state when no motifs', () => {
    render(<TacticalRadar motifs={[]} onMotifClick={onMotifClick} />);

    expect(screen.getByText(/No motif data yet/)).toBeInTheDocument();
  });

  it('should show "not enough motifs" when fewer than 3', () => {
    const motifs = [
      m({ name: 'Fork', accuracy: 0.8, rank: 'mastered' }),
      m({ name: 'Pin', accuracy: 0.7, rank: 'learning' }),
    ];

    render(<TacticalRadar motifs={motifs} onMotifClick={onMotifClick} />);

    expect(screen.getByText(/At least 3 different motifs/)).toBeInTheDocument();
  });

  it('should render radar chart with 3+ motifs', () => {
    const motifs = [
      m({ name: 'Fork', accuracy: 0.8, rank: 'mastered' }),
      m({ name: 'Pin', accuracy: 0.7, rank: 'learning' }),
      m({ name: 'Skewer', accuracy: 0.6, rank: 'learning' }),
    ];

    render(<TacticalRadar motifs={motifs} onMotifClick={onMotifClick} />);

    expect(screen.getByTestId('radar-chart')).toBeInTheDocument();
  });

  it('should show weakest motif with practice button', () => {
    const motifs = [
      m({ name: 'Fork', accuracy: 0.8, rank: 'mastered' }),
      m({ name: 'Pin', accuracy: 0.5, rank: 'needs_work' }),
      m({ name: 'Skewer', accuracy: 0.9, rank: 'mastered' }),
    ];

    render(<TacticalRadar motifs={motifs} onMotifClick={onMotifClick} />);

    expect(screen.getByText(/Pin \(50%\)/)).toBeInTheDocument();
    expect(screen.getByText('Practice Pin Now')).toBeInTheDocument();
  });

  it('should call onMotifClick when practice button is clicked', async () => {
    const motifs = [
      m({ name: 'Fork', accuracy: 0.8, rank: 'mastered' }),
      m({ name: 'Pin', accuracy: 0.5, rank: 'needs_work' }),
      m({ name: 'Skewer', accuracy: 0.9, rank: 'mastered' }),
    ];

    render(<TacticalRadar motifs={motifs} onMotifClick={onMotifClick} />);

    await user.click(screen.getByText('Practice Pin Now'));
    expect(onMotifClick).toHaveBeenCalledWith('Pin');
  });

  it('does not pick a low-sample motif as the weakest area', () => {
    // "Endgame" has the lowest accuracy but only one attempt — it must not be
    // presented as the weakness; the reliable "Pin" is chosen instead.
    const motifs = [
      m({ name: 'Fork', accuracy: 0.8, rank: 'mastered' }),
      m({ name: 'Pin', accuracy: 0.55, rank: 'needs_work' }),
      m({ name: 'Skewer', accuracy: 0.9, rank: 'mastered' }),
      m({ name: 'Endgame', accuracy: 0.0, rank: 'needs_work', attempts: 1, total_puzzles: 1, passed: 0, insufficient_data: true }),
    ];

    render(<TacticalRadar motifs={motifs} onMotifClick={onMotifClick} />);

    expect(screen.getByText('Practice Pin Now')).toBeInTheDocument();
    expect(screen.queryByText('Practice Endgame Now')).not.toBeInTheDocument();
  });

  it('should show mastery celebration when all motifs >= 85%', () => {
    const motifs = [
      m({ name: 'Fork', accuracy: 0.9, rank: 'mastered' }),
      m({ name: 'Pin', accuracy: 0.85, rank: 'mastered' }),
      m({ name: 'Skewer', accuracy: 0.95, rank: 'mastered' }),
    ];

    render(<TacticalRadar motifs={motifs} onMotifClick={onMotifClick} />);

    expect(screen.getByText('All Motifs Mastered!')).toBeInTheDocument();
  });

  it('should render heading', () => {
    render(<TacticalRadar motifs={[]} onMotifClick={onMotifClick} />);

    expect(screen.getByText(/Tactical Vision/)).toBeInTheDocument();
  });
});
