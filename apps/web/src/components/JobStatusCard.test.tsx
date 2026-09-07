import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { JobStatusCard } from './JobStatusCard';

describe('JobStatusCard', () => {
  const user = userEvent.setup();

  it('should not render when status is null', () => {
    const { container } = render(<JobStatusCard status={null} />);

    expect(container.firstChild).toBeNull();
  });

  it('presents queued work as waiting without invented progress', () => {
    render(<JobStatusCard status="queued" progress={0} message="Waiting in queue" />);

    expect(screen.getByText('Waiting to start')).toBeInTheDocument();
    expect(screen.getByText('Waiting in queue')).toBeInTheDocument();
    expect(screen.queryByText('Generating Puzzles…')).not.toBeInTheDocument();
    expect(screen.getByRole('progressbar', { name: 'Waiting for puzzle generation to start' }))
      .not.toHaveAttribute('aria-valuenow');
    expect(document.querySelector('[style*="width: 5%"]')).not.toBeInTheDocument();
  });

  it('should show "Generating Puzzles…" for running status', () => {
    render(<JobStatusCard status="running" progress={50} message="Processing..." />);

    expect(screen.getByText('Generating Puzzles…')).toBeInTheDocument();
  });

  it('uses only server-provided progress for a running job', () => {
    render(<JobStatusCard status="running" progress={75} />);

    const progressBar = screen.getByRole('progressbar', { name: 'Puzzle generation progress' });
    expect(progressBar).toHaveAttribute('aria-valuenow', '75');
    expect(progressBar.firstElementChild).toHaveStyle({ width: '75%' });
  });

  it('keeps running work indeterminate when the server omits progress', () => {
    render(<JobStatusCard status="running" message="Starting analysis" />);

    expect(screen.getByRole('progressbar', { name: 'Puzzle generation in progress' }))
      .not.toHaveAttribute('aria-valuenow');
  });

  it('keeps running work indeterminate when server progress is not finite', () => {
    render(<JobStatusCard status="running" progress={Number.NaN} />);

    expect(screen.getByRole('progressbar', { name: 'Puzzle generation in progress' }))
      .not.toHaveAttribute('aria-valuenow');
  });

  it('should show "Generation Complete" for succeeded status', () => {
    render(<JobStatusCard status="succeeded" />);

    expect(screen.getByText('Generation Complete')).toBeInTheDocument();
    expect(screen.getByText('Ready to solve!')).toBeInTheDocument();
  });

  it('should show "Generation Failed" for failed status', () => {
    render(<JobStatusCard status="failed" message="Error occurred" error="Something went wrong" />);

    expect(screen.getByText('Generation Failed')).toBeInTheDocument();
    expect(screen.getByText('Something went wrong')).toBeInTheDocument();
  });

  it('should show a truthful terminal heading for canceled status', () => {
    render(<JobStatusCard status="canceled" message="Canceled by user" />);

    expect(screen.getByText('Generation Canceled')).toBeInTheDocument();
    expect(screen.getByText('Canceled by user')).toBeInTheDocument();
    expect(screen.queryByRole('progressbar')).not.toBeInTheDocument();
  });

  it('should show cancel button when onCancel is provided and processing', () => {
    const onCancel = vi.fn();
    render(<JobStatusCard status="running" onCancel={onCancel} />);

    expect(screen.getByText('Cancel')).toBeInTheDocument();
  });

  it('should call onCancel when cancel button is clicked', async () => {
    const onCancel = vi.fn();
    render(<JobStatusCard status="running" onCancel={onCancel} />);

    await user.click(screen.getByText('Cancel'));
    expect(onCancel).toHaveBeenCalledTimes(1);
  });

  it('should not show cancel button when not processing', () => {
    const onCancel = vi.fn();
    render(<JobStatusCard status="succeeded" onCancel={onCancel} />);

    expect(screen.queryByText('Cancel')).not.toBeInTheDocument();
  });

  it('should show the hint while processing', () => {
    render(<JobStatusCard status="running" hint="this usually takes 2-3 minutes" />);

    expect(screen.getByText('this usually takes 2-3 minutes')).toBeInTheDocument();
  });

  it('should not show the hint when not processing', () => {
    render(<JobStatusCard status="succeeded" hint="this usually takes 2-3 minutes" />);

    expect(screen.queryByText('this usually takes 2-3 minutes')).not.toBeInTheDocument();
  });
});
