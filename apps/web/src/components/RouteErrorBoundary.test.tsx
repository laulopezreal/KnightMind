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

  // Keyed on location rather than pathname so the current page's own nav link
  // retries instead of looking like a dead button.
  it('retries when the user re-clicks the link for the page they are on', async () => {
    render(<Harness />);
    expect(screen.getByRole('alert')).toBeInTheDocument();

    shouldPageThrow = false;
    await user.click(screen.getByRole('link', { name: 'Dashboard' }));

    expect(screen.getByText('Dashboard content')).toBeInTheDocument();
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });

  // Focus falls to <body> when the fallback mounts, which drops a keyboard
  // user at the top of the document — worst on a repeated failure, where the
  // button they just pressed is unmounted out from under them.
  it('moves focus to the alert, and back to it when Try Again throws again', async () => {
    render(<Harness />);

    const alert = screen.getByRole('alert');
    expect(document.activeElement).toBe(alert);

    // Still broken: pressing Try Again re-throws and remounts the fallback.
    await user.click(screen.getByRole('button', { name: /try loading this page again/i }));

    expect(document.activeElement).toBe(screen.getByRole('alert'));
    expect(document.activeElement).not.toBe(document.body);
  });

  it('offers a report link carrying the route and the message', () => {
    render(<Harness />);

    const link = screen.getByRole('link', { name: 'Report this' });
    const href = link.getAttribute('href') ?? '';
    // URLSearchParams encodes spaces as "+", which decodeURIComponent leaves alone.
    const decoded = decodeURIComponent(href).replace(/\+/g, ' ');

    expect(href).toContain('/issues/new?');
    expect(decoded).toContain('/dashboard');
    expect(decoded).toContain("reading 'color'");
  });

  it('titles the fallback as the page-level heading', () => {
    render(<Harness />);

    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('This page didn’t load');
  });
});
