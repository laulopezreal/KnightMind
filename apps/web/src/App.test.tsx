import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';

// The page is lazy-loaded, so the mock has to stand in for the module React
// imports. '/' is where jsdom starts, so Home is the page under test.
vi.mock('./pages/Home', () => ({
  default: () => {
    throw new Error('Cannot read properties of undefined (reading \'color\')');
  },
}));

import App from './App';

/**
 * The component tests prove the boundary resets and recovers; they cannot prove
 * it is mounted in the right place, because each builds its own harness. This
 * one renders the real App, so hoisting RouteErrorBoundary above Layout — which
 * silently restores the whole-shell crash this was written to fix — fails here.
 */
describe('App error containment', () => {
  beforeEach(() => {
    vi.spyOn(console, 'error').mockImplementation(() => {});

    // jsdom has no matchMedia, and ThemeProvider reads it during render — from
    // outside the route boundary, so without this the root boundary catches
    // that instead and the assertions below would pass or fail for the wrong
    // reason. (That it caught it is itself the root boundary doing its job.)
    window.matchMedia = ((query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addEventListener: () => {},
      removeEventListener: () => {},
      addListener: () => {},
      removeListener: () => {},
      dispatchEvent: () => false,
    })) as unknown as typeof window.matchMedia;
  });

  it('keeps the shell mounted when a page throws', async () => {
    render(<App />);

    // Suspense resolves the lazy page, which throws on first render.
    expect(await screen.findByRole('alert')).toHaveTextContent('This page didn’t load');

    // The chrome that the root-level boundary used to take down with it.
    expect(screen.getByRole('complementary', { name: 'Sidebar' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Dashboard' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Report a problem' })).toBeInTheDocument();

    // The root boundary's own fallback must not be what is showing.
    expect(screen.queryByText('Something went wrong')).not.toBeInTheDocument();
  });
});
