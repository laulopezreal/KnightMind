import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { LoadingSpinner } from './LoadingSpinner';

describe('LoadingSpinner', () => {
  it('should render with default size and label', () => {
    render(<LoadingSpinner />);

    const spinner = screen.getByRole('status');
    expect(spinner).toBeInTheDocument();
    expect(spinner).toHaveAttribute('aria-label', 'Loading...');
  });

  it('should render with custom label', () => {
    render(<LoadingSpinner label="Fetching puzzles..." />);

    const spinner = screen.getByRole('status');
    expect(spinner).toHaveAttribute('aria-label', 'Fetching puzzles...');
  });

  it('should apply small size classes', () => {
    render(<LoadingSpinner size="sm" />);

    const spinner = screen.getByRole('status');
    expect(spinner.className).toContain('h-4');
    expect(spinner.className).toContain('w-4');
  });

  it('should apply medium size classes by default', () => {
    render(<LoadingSpinner />);

    const spinner = screen.getByRole('status');
    expect(spinner.className).toContain('h-12');
    expect(spinner.className).toContain('w-12');
  });

  it('should apply large size classes', () => {
    render(<LoadingSpinner size="lg" />);

    const spinner = screen.getByRole('status');
    expect(spinner.className).toContain('h-16');
    expect(spinner.className).toContain('w-16');
  });

  it('should have spin animation class', () => {
    render(<LoadingSpinner />);

    const spinner = screen.getByRole('status');
    expect(spinner.className).toContain('animate-spin');
  });
});
