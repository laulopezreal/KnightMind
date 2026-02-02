import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { WarmupSummary } from './WarmupSummary';

const mockSessionSummary = {
  session_id: 'warmup-1',
  requested_n: 5,
  pass_count: 4,
  fail_count: 1,
  total_time_ms: 60000,
  current_streak: 4,
  best_streak: 4,
  hints_used: 0,
  created_at: '2025-01-15T11:59:00Z',
  completed_at: '2025-01-15T12:00:00Z',
};

describe('WarmupSummary', () => {
  const user = userEvent.setup();

  it('should display warmup complete heading', () => {
    render(<WarmupSummary sessionSummary={mockSessionSummary} onContinue={vi.fn()} />);

    expect(screen.getByText(/Warmup Complete!/)).toBeInTheDocument();
  });

  it('should display accuracy percentage', () => {
    render(<WarmupSummary sessionSummary={mockSessionSummary} onContinue={vi.fn()} />);

    // 4 pass, 1 fail = 80%
    expect(screen.getByText('80%')).toBeInTheDocument();
  });

  it('should display pass and fail counts', () => {
    render(<WarmupSummary sessionSummary={mockSessionSummary} onContinue={vi.fn()} />);

    expect(screen.getByText('4')).toBeInTheDocument();
    expect(screen.getByText('1')).toBeInTheDocument();
  });

  it('should show high retention feedback for >= 80%', () => {
    render(<WarmupSummary sessionSummary={mockSessionSummary} onContinue={vi.fn()} />);

    expect(screen.getByText(/Great retention/)).toBeInTheDocument();
  });

  it('should show moderate feedback for 60-79%', () => {
    const summary = { ...mockSessionSummary, pass_count: 3, fail_count: 2 };
    render(<WarmupSummary sessionSummary={summary} onContinue={vi.fn()} />);

    expect(screen.getByText(/Some patterns need brushing up/)).toBeInTheDocument();
  });

  it('should show low retention feedback for < 60%', () => {
    const summary = { ...mockSessionSummary, pass_count: 1, fail_count: 4 };
    render(<WarmupSummary sessionSummary={summary} onContinue={vi.fn()} />);

    expect(screen.getByText(/Time to rebuild/)).toBeInTheDocument();
  });

  it('should call onContinue when button clicked', async () => {
    const onContinue = vi.fn();
    render(<WarmupSummary sessionSummary={mockSessionSummary} onContinue={onContinue} />);

    await user.click(screen.getByText('Continue to Dashboard'));
    expect(onContinue).toHaveBeenCalledTimes(1);
  });

  it('should have accessible region', () => {
    render(<WarmupSummary sessionSummary={mockSessionSummary} onContinue={vi.fn()} />);

    const section = screen.getByRole('region', { name: /warmup/i });
    expect(section).toBeInTheDocument();
  });
});
