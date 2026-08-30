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
  ResponsiveContainer: ({ children, height }: { children: React.ReactNode; height: number }) => (
    <div data-testid="responsive-container" data-height={height}>{children}</div>
  ),
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
        start_accuracy: 0.7,
        end_accuracy: 0.85,
        total_reviews: 20,
        insufficient_data: false,
        data_points: [
          { date: '2025-01-10', accuracy: 0.7 },
          { date: '2025-01-15', accuracy: 0.85 },
        ],
      },
    ];

    render(<MotifTrends trends={trends} windowDays={30} />);

    expect(screen.getByTestId('line-chart')).toBeInTheDocument();
  });

  it('should provide a concrete responsive height for the trend chart', () => {
    const trends = [{
      motif: 'Fork',
      trend: 'up' as const,
      change: 0.15,
      start_accuracy: 0.7,
      end_accuracy: 0.85,
      total_reviews: 20,
      insufficient_data: false,
      data_points: [
        { date: '2025-01-10', accuracy: 0.7 },
        { date: '2025-01-15', accuracy: 0.85 },
      ],
    }];

    render(<MotifTrends trends={trends} windowDays={30} />);

    expect(screen.getByTestId('responsive-container')).toHaveAttribute('data-height', '320');
  });

  it('should show trend summaries for up to 3 motifs', () => {
    const trends = [
      { motif: 'Fork', trend: 'up' as const, change: 0.15, start_accuracy: 0.7, end_accuracy: 0.85, total_reviews: 20, insufficient_data: false, data_points: [{ date: '2025-01-15', accuracy: 0.85 }] },
      { motif: 'Pin', trend: 'down' as const, change: -0.1, start_accuracy: 0.7, end_accuracy: 0.6, total_reviews: 18, insufficient_data: false, data_points: [{ date: '2025-01-15', accuracy: 0.6 }] },
    ];

    render(<MotifTrends trends={trends} windowDays={30} />);

    expect(screen.getByText('Fork')).toBeInTheDocument();
    expect(screen.getByText('Pin')).toBeInTheDocument();
  });

  it('shows "Limited data" instead of a direction for low-sample motifs', () => {
    const trends = [
      { motif: 'Skewer', trend: 'steady' as const, change: -1.0, start_accuracy: 1.0, end_accuracy: 0.0, total_reviews: 2, insufficient_data: true, data_points: [{ date: '2025-01-10', accuracy: 1.0 }, { date: '2025-01-15', accuracy: 0.0 }] },
    ];

    render(<MotifTrends trends={trends} windowDays={30} />);

    // The misleading "-100.0%" must not appear; a neutral label shows instead.
    expect(screen.getByText(/Limited data/)).toBeInTheDocument();
    expect(screen.queryByText(/-100\.0%/)).not.toBeInTheDocument();
  });
});
