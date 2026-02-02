import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { ReportProblem } from './ReportProblem';

describe('ReportProblem', () => {
  it('should render a link to GitHub issues', () => {
    render(<ReportProblem />);

    const link = screen.getByRole('link', { name: /report a problem/i });
    expect(link).toBeInTheDocument();
    expect(link).toHaveAttribute('href', 'https://github.com/laulopezreal/KnightMind/issues');
  });

  it('should open in a new tab', () => {
    render(<ReportProblem />);

    const link = screen.getByRole('link');
    expect(link).toHaveAttribute('target', '_blank');
    expect(link).toHaveAttribute('rel', 'noopener noreferrer');
  });

  it('should have accessible label', () => {
    render(<ReportProblem />);

    const link = screen.getByLabelText('Report a problem');
    expect(link).toBeInTheDocument();
  });
});
