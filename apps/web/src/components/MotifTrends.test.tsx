import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MotifTrends } from './MotifTrends';

// Mock recharts to avoid SVG rendering issues in jsdom
vi.mock('recharts', () => ({
  LineChart: ({ children }: { children: React.ReactNode }) => <div data-testid="line-chart">{children}</div>,
  Line: () => <div data-testid="line" />,
  XAxis: () => <div />,
  YAxis: () => <div />,
  CartesianGrid: () => <div />,
  Tooltip: () => <div />,
  Legend: () => <div />,
  ResponsiveContainer: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
}));

describe('MotifTrends', () => {
  it('should show empty state when no trends', () => {
    render(<MotifTrends trends={[]} windowDays={30} />);

    expect(screen.getByText(/Complete more puzzles/)).toBeInTheDocument();
    expect(screen.getByText(/30 days/)).toBeInTheDocument();
  });

  it('should render heading', () => {
    render(<MotifTrends trends={[]} windowDays={30} />);

    expect(screen.getByText(/Progress Trends/)).toBeInTheDocument();
  });

  it('should render chart when trends are provided', () => {
    const trends = [
      {
        motif: 'Fork',
        trend: 'up' as const,
        change: 0.15,
        data_points: [
          { date: '2025-01-10', accuracy: 0.7, count: 5 },
          { date: '2025-01-15', accuracy: 0.85, count: 8 },
        ],
      },
    ];

    render(<MotifTrends trends={trends} windowDays={30} />);

    expect(screen.getByTestId('line-chart')).toBeInTheDocument();
  });

  it('should show trend summaries for up to 3 motifs', () => {
    const trends = [
      { motif: 'Fork', trend: 'up' as const, change: 0.15, data_points: [{ date: '2025-01-15', accuracy: 0.85, count: 5 }] },
      { motif: 'Pin', trend: 'down' as const, change: -0.1, data_points: [{ date: '2025-01-15', accuracy: 0.6, count: 3 }] },
    ];

    render(<MotifTrends trends={trends} windowDays={30} />);

    expect(screen.getByText('Fork')).toBeInTheDocument();
    expect(screen.getByText('Pin')).toBeInTheDocument();
  });
});
