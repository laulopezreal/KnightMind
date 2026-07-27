import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { ReportProblem } from './ReportProblem';

function renderWithRoute(initialPath = '/') {
  return render(
    <MemoryRouter initialEntries={[initialPath]}>
      <ReportProblem />
    </MemoryRouter>
  );
}

describe('ReportProblem', () => {
  it('should render a link to GitHub issues', () => {
    renderWithRoute();

    const link = screen.getByRole('link', { name: /report a problem/i });
    expect(link).toBeInTheDocument();
    expect(link).toHaveAttribute('href', 'https://github.com/laulopezreal/KnightMind/issues');
  });

  it('should open in a new tab', () => {
    renderWithRoute();

    const link = screen.getByRole('link');
    expect(link).toHaveAttribute('target', '_blank');
    expect(link).toHaveAttribute('rel', 'noopener noreferrer');
  });

  it('should have accessible label', () => {
    renderWithRoute();

    const link = screen.getByLabelText('Report a problem');
    expect(link).toBeInTheDocument();
  });

  it('should have a 44px touch target', () => {
    renderWithRoute();

    const link = screen.getByRole('link', { name: /report a problem/i });
    expect(link).toHaveClass('h-11');
    expect(link).toHaveClass('w-11');
  });

  it('should use flex on non-puzzle routes', () => {
    renderWithRoute('/dashboard');

    const link = screen.getByRole('link', { name: /report a problem/i });
    expect(link).toHaveClass('flex');
    expect(link).not.toHaveClass('hidden');
  });

  it('should be hidden on mobile on the puzzle route', () => {
    renderWithRoute('/puzzles');

    const link = screen.getByRole('link', { name: /report a problem/i });
    // hidden md:flex — not visible on mobile, visible on desktop
    expect(link).toHaveClass('hidden');
    expect(link).toHaveClass('md:flex');
    expect(link).not.toHaveClass('flex');
  });
});
