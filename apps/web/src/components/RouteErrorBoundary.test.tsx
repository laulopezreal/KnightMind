import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Routes, Route, Link } from 'react-router-dom';
import { RouteErrorBoundary } from './RouteErrorBoundary';

let shouldPageThrow = true;

function BrokenPage() {
  if (shouldPageThrow) {
    throw new Error('Cannot read properties of undefined (reading \'color\')');
  }
  return <p>Dashboard content</p>;
}

/**
 * Mirrors App.tsx: the nav lives outside the boundary (as Layout's sidebar
 * does), the routes inside it.
 */
function Harness() {
  return (
    <MemoryRouter initialEntries={['/dashboard']}>
      <nav aria-label="Primary">
        <Link to="/insights">Insights</Link>
        <Link to="/dashboard">Dashboard</Link>
      </nav>
      <RouteErrorBoundary>
        <Routes>
          <Route path="/dashboard" element={<BrokenPage />} />
          <Route path="/insights" element={<p>Insights content</p>} />
        </Routes>
      </RouteErrorBoundary>
    </MemoryRouter>
  );
}

describe('RouteErrorBoundary', () => {
  let user: ReturnType<typeof userEvent.setup>;

  beforeEach(() => {
    user = userEvent.setup();
    vi.spyOn(console, 'error').mockImplementation(() => {});
    shouldPageThrow = true;
  });

  it('renders the page when nothing throws', () => {
    shouldPageThrow = false;
    render(<Harness />);

    expect(screen.getByText('Dashboard content')).toBeInTheDocument();
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });

  // The whole point of moving the boundary inside Layout: the shell survives.
  it('keeps the surrounding nav usable when a page throws', () => {
    render(<Harness />);

    expect(screen.getByRole('alert')).toHaveTextContent('This page didn’t load');
    expect(screen.getByRole('navigation', { name: 'Primary' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Insights' })).toBeInTheDocument();
  });

  it('surfaces the underlying error message', () => {
    render(<Harness />);

    expect(
      screen.getByText("Cannot read properties of undefined (reading 'color')"),
    ).toBeInTheDocument();
  });

  // Without the pathname reset key the boundary stays caught, so the user
  // navigates away and lands on the dead page's error instead of the new page.
  it('recovers when the user navigates to another page', async () => {
    render(<Harness />);
    expect(screen.getByRole('alert')).toBeInTheDocument();

    await user.click(screen.getByRole('link', { name: 'Insights' }));

    expect(screen.getByText('Insights content')).toBeInTheDocument();
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });

  it('re-renders the page on Try Again', async () => {
    render(<Harness />);

    shouldPageThrow = false;
    await user.click(screen.getByRole('button', { name: /try loading this page again/i }));

    expect(screen.getByText('Dashboard content')).toBeInTheDocument();
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });
});
